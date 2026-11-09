"""Exact finite execution-graph search to completed delivery, including unloading.

All departures are explicit scheduled cargo services. Handling with no appointment
is mandatory immediate processing on readiness, NOT a discretized flexible window.
An action ID is one accepted execution/obligation and is consumed at most once.
"""

import heapq
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal

from a2_engine.freight.delivery.models import (
    CargoAction,
    CargoDeliveryPath,
    CargoDeliveryScope,
    CargoInterval,
    CargoModeAnalysis,
    CargoReadiness,
    CargoState,
    IntercityCargoAnalysis,
)
from a2_engine.freight.intercity import intercity_cargo_index
from a2_engine.freight.models import CargoCompatibility, CargoProfile, compatibility_state
from a2_engine.shared.config import CargoDeliveryRules, FreightConfig
from a2_engine.shared.normalized import advance, content_hash, ids, utc


@dataclass(frozen=True)
class _Label:
    state_id: str
    at: datetime
    origin_node: str
    used: frozenset[str]
    modes: tuple[str, ...]
    first_departure_at: datetime | None
    actions: tuple[str, ...]
    intervals: tuple[CargoInterval, ...]


def analyze_intercity_cargo(
    scope: CargoDeliveryScope,
    started_at: datetime,
    profile: CargoProfile,
    states: tuple[CargoState, ...],
    actions: tuple[CargoAction, ...],
    readiness: tuple[CargoReadiness, ...],
    *,
    config: FreightConfig,
    rules: CargoDeliveryRules,
    resource_limit: int | None = None,
) -> IntercityCargoAnalysis:
    start = utc(started_at)
    if not scope.start_at <= start < scope.end_at:
        raise ValueError("CARGO_START_OUTSIDE_SCOPE")
    if (
        scope.context.methodology_version != config.methodology_version
        or rules.methodology_version != config.methodology_version
        or scope.requirements_hash != profile.requirements_hash
        or profile.urgency not in config.delivery_half_life_seconds
    ):
        raise ValueError("CARGO_ANALYSIS_PROFILE_MISMATCH")
    if resource_limit is not None and (type(resource_limit) is not int or resource_limit < 0):
        raise ValueError("NONNEGATIVE_CARGO_RESOURCE_LIMIT_REQUIRED")
    try:
        window_end = start + timedelta(days=rules.departure_window_days)
    except OverflowError as exc:
        raise ValueError("CARGO_DEPARTURE_WINDOW_OVERFLOW") from exc
    if (
        any(not isinstance(s, CargoState) for s in states)
        or any(not isinstance(a, CargoAction) for a in actions)
        or any(not isinstance(r, CargoReadiness) for r in readiness)
    ):
        raise ValueError("NORMALIZED_CARGO_INPUT_REQUIRED")
    states = tuple(sorted(states, key=lambda s: s.state_id))
    actions = tuple(sorted(actions, key=lambda a: a.action_id))
    readiness = tuple(sorted(readiness, key=lambda r: r.state_id))
    ids(tuple(s.state_id for s in states), required=False)
    ids(tuple(a.action_id for a in actions), required=False)
    ids(tuple(r.state_id for r in readiness), required=False)
    state_by_id = {s.state_id: s for s in states}
    action_by_id = {a.action_id: a for a in actions}
    if (set(scope.origin_node_ids) | set(scope.destination_node_ids)) - {s.node_id for s in states}:
        raise ValueError("UNKNOWN_CARGO_BOUNDARY")
    reasons = set()
    for name in (
        "services_complete",
        "calendars_complete",
        "operations_complete",
        "compatibility_complete",
        "modes_complete",
    ):
        if not getattr(scope, name):
            reasons.add(name.upper() + "_INCOMPLETE")
    if scope.end_at < window_end:
        reasons.add("FIRST_DEPARTURE_WINDOW_UNCOVERED")
    evidence = set(scope.evidence_refs)
    excluded: set[tuple[str, str]] = set()

    def check(
        receipt: CargoCompatibility,
        from_id: str,
        to_id: str,
        service: str,
        at: datetime,
        end: datetime,
    ) -> Literal["confirmed", "forbidden", "unknown"]:
        evidence.update(receipt.evidence_refs)
        return compatibility_state(
            receipt,
            context=scope.context,
            profile=profile,
            from_id=from_id,
            to_id=to_id,
            service_id=service,
            direction="delivery",
            start_at=at,
            end_at=end,
        )

    adjacency: dict[str, list[CargoAction]] = {}
    for action in actions:
        if action.from_state_id not in state_by_id or action.to_state_id not in state_by_id:
            raise ValueError("DANGLING_CARGO_ACTION")
        source, target = state_by_id[action.from_state_id], state_by_id[action.to_state_id]
        if source.stage == "completed":
            raise ValueError("CARGO_COMPLETED_STATE_IS_TERMINAL")
        if action.kind == "movement":
            if (
                source.stage not in ("prepared", "in_transit")
                or target.stage != "in_transit"
                or source.execution_id != action.service_id
                or target.execution_id != action.service_id
            ):
                raise ValueError("CARGO_MOVEMENT_REQUIRES_PREPARED_EXECUTION")
        elif source.node_id != target.node_id:
            raise ValueError("HANDLING_CANNOT_TELEPORT_CARGO")
        for requirement in action.requires_operations:
            if requirement not in action_by_id or action_by_id[requirement].kind != "handling":
                raise ValueError("UNKNOWN_REQUIRED_CARGO_OPERATION")
        check(action.compatibility, source.node_id, target.node_id, action.service_id, start, start)
        if action.wait_policy is not None:
            check(
                action.wait_policy.compatibility,
                source.node_id,
                source.node_id,
                action.service_id,
                start,
                start,
            )
        evidence.update(action.evidence_refs + action.calendar_evidence_refs)
        if action.active is None or not action.calendar_evidence_refs:
            reasons.add("CARGO_CALENDAR_UNKNOWN")
        if action.necessity == "unknown" or action.seconds is None:
            reasons.add("CARGO_OPERATION_OR_MOVEMENT_UNKNOWN")
        if action.compatibility.state == "unknown":
            reasons.add("CARGO_COMPATIBILITY_UNKNOWN")
        adjacency.setdefault(action.from_state_id, []).append(action)
    queue: list[tuple[datetime, int, tuple[str, ...], int, _Label]] = []
    serial = 0
    seen: set[tuple[object, ...]] = set()
    complete_paths: dict[str, CargoDeliveryPath] = {}
    incomplete: dict[str, CargoDeliveryPath] = {}

    def add(label: _Label) -> None:
        nonlocal serial
        serial += 1
        heapq.heappush(queue, (label.at, len(label.actions), label.actions, serial, label))
        state = state_by_id[label.state_id]
        if (
            state.stage == "completed"
            and state.node_id in scope.destination_node_ids
            and label.first_departure_at is not None
        ):
            candidate = path(label)
            complete_paths[candidate.path_id] = candidate

    def path(
        label: _Label, blocked: str | None = None, reason: str | None = None
    ) -> CargoDeliveryPath:
        values = {
            key: sum(
                (i.end_at - i.start_at for i in label.intervals if i.kind == key), timedelta()
            ).total_seconds()
            for key in ("initial_wait", "movement", "handling", "transfer_wait")
        }
        evaluation = intercity_cargo_index(
            cargo_profile_id=profile.profile_id,
            urgency=profile.urgency,
            initial_wait_seconds=values["initial_wait"],
            movement_seconds=values["movement"],
            handling_seconds=values["handling"] if blocked is None else None,
            transfer_wait_seconds=values["transfer_wait"],
            compatibility_confirmed=reason
            not in ("CARGO_COMPATIBILITY_UNKNOWN", "CARGO_WAIT_CONDITIONS_UNKNOWN"),
            config=config,
        )
        if blocked is None and evaluation.total_seconds.value != (label.at - start).total_seconds():
            # fsum(float components) can differ below an ULP; timestamps are exact at microseconds.
            from math import isclose

            if not isclose(
                evaluation.total_seconds.value or 0,
                (label.at - start).total_seconds(),
                rel_tol=1e-12,
                abs_tol=1e-6,
            ):
                raise ValueError("OVERLAPPING_OR_MISSING_CARGO_INTERVAL")
        path_id = "cargo-path:" + content_hash((scope, profile, start, label, blocked, reason))
        return CargoDeliveryPath(
            path_id,
            label.origin_node,
            state_by_id[label.state_id].node_id,
            label.at if blocked is None else None,
            label.modes,
            label.actions,
            label.intervals,
            evaluation,
            blocked,
            reason,
        )

    for ready in readiness:
        if ready.state_id not in state_by_id:
            raise ValueError("UNKNOWN_CARGO_READINESS_STATE")
        state = state_by_id[ready.state_id]
        if state.node_id not in scope.origin_node_ids or state.stage != "unprepared":
            raise ValueError("CARGO_READINESS_BOUNDARY_MISMATCH")
        status = check(
            ready.compatibility,
            state.node_id,
            state.node_id,
            "readiness:" + state.state_id,
            start,
            start,
        )
        if status == "forbidden":
            excluded.add((ready.state_id, "READINESS_FORBIDDEN"))
        elif status != "confirmed" or ready.ready_at != start:
            reasons.add("INITIAL_CARGO_READINESS_UNKNOWN")
        else:
            add(_Label(state.state_id, start, state.node_id, frozenset(), (), None, (), ()))
    if not readiness:
        reasons.add("INITIAL_CARGO_READINESS_UNKNOWN")
    expected_ready = {
        s.state_id for s in states if s.stage == "unprepared" and s.node_id in scope.origin_node_ids
    }
    if expected_ready - {r.state_id for r in readiness}:
        reasons.add("INITIAL_CARGO_READINESS_ROSTER_INCOMPLETE")
    expanded = 0
    interrupted = False
    while queue:
        _, _, _, _, label = heapq.heappop(queue)
        key = (
            label.state_id,
            label.at,
            label.origin_node,
            label.used,
            label.modes,
            label.first_departure_at,
        )
        if key in seen:
            continue
        if resource_limit is not None and expanded >= resource_limit:
            interrupted = True
            break
        seen.add(key)
        expanded += 1
        state = state_by_id[label.state_id]
        if state.stage == "completed":
            if state.node_id in scope.destination_node_ids and label.first_departure_at is not None:
                result = path(label)
                complete_paths[result.path_id] = result
            continue
        for action in adjacency.get(label.state_id, ()):
            if action.action_id in label.used or not set(action.requires_operations) <= label.used:
                continue
            if action.active is not True or not action.calendar_evidence_refs:
                excluded.add(
                    (
                        action.action_id,
                        "CALENDAR_INACTIVE" if action.active is False else "CALENDAR_UNKNOWN",
                    )
                )
                continue
            if action.kind == "movement" and action.mode not in scope.allowed_modes:
                continue
            at = action.scheduled_at if action.scheduled_at is not None else label.at
            if at < label.at:
                continue
            if (
                action.kind == "movement"
                and label.first_departure_at is None
                and not start <= at < window_end
            ):
                continue
            end = advance(at, action.seconds or 0)
            status = check(
                action.compatibility,
                state.node_id,
                state_by_id[action.to_state_id].node_id,
                action.service_id,
                at,
                end,
            )
            reason = None
            if status == "forbidden":
                excluded.add((action.action_id, "CARGO_COMPATIBILITY_FORBIDDEN"))
                continue
            if status == "unknown":
                reason = "CARGO_COMPATIBILITY_UNKNOWN"
            elif action.seconds is None or action.necessity == "unknown":
                reason = "REQUIRED_CARGO_COMPONENT_UNKNOWN"
            intervals = label.intervals
            if at > label.at:
                policy = action.wait_policy
                if policy is None:
                    reason = "CARGO_WAIT_CONDITIONS_UNKNOWN"
                else:
                    expected = (
                        "initial_wait" if label.first_departure_at is None else "transfer_wait"
                    )
                    if policy.kind != expected:
                        raise ValueError("CARGO_WAIT_COMPONENT_SEMANTICS_MISMATCH")
                    wait_state = check(
                        policy.compatibility,
                        state.node_id,
                        state.node_id,
                        action.service_id,
                        label.at,
                        at,
                    )
                    if wait_state == "forbidden":
                        excluded.add((action.action_id, "CARGO_WAIT_FORBIDDEN"))
                        continue
                    if wait_state == "unknown":
                        reason = "CARGO_WAIT_CONDITIONS_UNKNOWN"
                    else:
                        intervals += (
                            CargoInterval(
                                action.action_id,
                                policy.kind,
                                state.state_id,
                                state.state_id,
                                label.at,
                                at,
                                policy.compatibility.evidence_refs,
                            ),
                        )
            if reason is not None:
                reasons.add(reason)
                result = path(replace(label, intervals=intervals, at=at), action.action_id, reason)
                incomplete[result.path_id] = result
                continue
            if end > scope.end_at:
                reasons.add("CARGO_CONTINUATION_OUTSIDE_HORIZON")
                result = path(label, action.action_id, "CARGO_CONTINUATION_OUTSIDE_HORIZON")
                incomplete[result.path_id] = result
                continue
            intervals += (
                CargoInterval(
                    action.action_id,
                    action.kind,
                    action.from_state_id,
                    action.to_state_id,
                    at,
                    end,
                    tuple(
                        sorted(
                            set(
                                action.evidence_refs
                                + action.compatibility.evidence_refs
                                + action.calendar_evidence_refs
                            )
                        )
                    ),
                ),
            )
            modes = tuple(
                sorted(set(label.modes) | ({action.mode} if action.mode is not None else set()))
            )
            first = (
                at
                if action.kind == "movement" and label.first_departure_at is None
                else label.first_departure_at
            )
            add(
                _Label(
                    action.to_state_id,
                    end,
                    label.origin_node,
                    label.used | {action.action_id},
                    modes,
                    first,
                    label.actions + (action.action_id,),
                    intervals,
                )
            )
    data_complete = not reasons
    if interrupted:
        reasons.add("RESOURCE_LIMIT_REACHED")
    rows = tuple(sorted(complete_paths.values(), key=lambda p: p.path_id))

    def select(category: str) -> CargoModeAnalysis:
        choices = [p for p in rows if category == "overall" or p.category == category]
        best = (
            min(choices, key=lambda p: (p.completed_at, len(p.action_ids), p.action_ids, p.path_id))
            if choices
            else None
        )
        return CargoModeAnalysis(category, best, data_complete and not interrupted)

    overall = select("overall")
    connection: Literal["reachable", "confirmed_absent", "unknown"] = (
        "reachable"
        if rows
        else "confirmed_absent"
        if data_complete and not interrupted
        else "unknown"
    )
    identity = content_hash(
        (
            "cargo-completion-v1",
            scope,
            profile,
            start,
            states,
            actions,
            readiness,
            rules,
            tuple(sorted(config.delivery_half_life_seconds.items())),
            resource_limit,
        )
    )
    return IntercityCargoAnalysis(
        "cargo-analysis:" + identity,
        scope,
        profile,
        start,
        window_end,
        states,
        actions,
        readiness,
        rows,
        tuple(sorted(incomplete.values(), key=lambda p: p.path_id)),
        tuple(select(mode) for mode in scope.allowed_modes + ("mixed",)),
        overall,
        connection,
        data_complete,
        not interrupted,
        expanded,
        interrupted,
        overall.optimum_proven and overall.best_known is not None,
        tuple(sorted(excluded)),
        tuple(sorted(reasons)),
        tuple(sorted(evidence)),
    )
