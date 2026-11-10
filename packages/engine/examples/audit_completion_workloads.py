"""Bounded deterministic inputs and independent enumeration for audit completion."""

import random
import runpy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

from a2_engine import (
    PhysicalDeparture,
    PhysicalService,
    TimetableEvent,
    TimetableRun,
    TimetableTransfer,
    TransportModeBinding,
    UrbanODCollector,
    UrbanODObservation,
    UrbanODPair,
    analyze_intercity_cargo,
    analyze_intercity_passenger,
    build_reference,
    calculate_urban_tau,
    load_cargo_delivery_rules,
    load_freight_config,
    load_intercity_passenger_rules,
    load_passenger_config,
    load_urban_tau_rules,
)


def demo(name: str) -> dict[str, Any]:
    return runpy.run_path(str(Path(__file__).with_name(f"synthetic_{name}.py")))


def timetable_case(
    size: int, seed: int = 314159
) -> tuple[Any, Any, list[tuple[str, str, int, int, str, str]]]:
    m = demo("intercity")
    scope, table = m["demo_inputs"]()
    rng = random.Random(seed)
    raw = []
    runs = []
    nodes = ("A", "X", "Y", "B")
    for i in range(size):
        a, b = sorted(rng.sample(range(4), 2))
        dep = rng.randrange(0, 60000)
        arr = dep + rng.randrange(1, 6000)
        mode = rng.choice(("rail", "air", "road"))
        run = TimetableRun(
            "snapshot",
            "audit",
            f"r{i}",
            m["START"].date(),
            "UTC",
            mode,
            tuple(
                TimetableEvent(
                    seq,
                    node,
                    m["START"] + timedelta(seconds=t),
                    m["START"] + timedelta(seconds=t),
                    True,
                    True,
                    m["PROOF"],
                )
                for seq, node, t in ((0, nodes[a], dep), (1, nodes[b], arr))
            ),
            True,
            m["PROOF"],
        )
        runs.append(run)
        raw.append((nodes[a], nodes[b], dep, arr, mode, run.run_id))
    table = replace(
        table,
        scope=replace(table.scope, node_ids=frozenset(nodes)),
        runs=tuple(runs),
        transfers=tuple(TimetableTransfer(f"t:{n}", n, n, 120, True, m["PROOF"]) for n in nodes),
    )
    return scope, table, raw


def enumerate_schedule(
    raw: list[tuple[str, str, int, int, str, str]], wait_limit: int | None = None
) -> list[tuple[int, int, tuple[str, ...], tuple[str, ...]]]:
    answers = []

    def walk(
        node: str, at: int, waiting: int, first: int, modes: tuple[str, ...], path: tuple[str, ...]
    ) -> None:
        if node == "B":
            answers.append((first, at, modes, path))
            return
        for a, b, dep, arr, mode, identifier in raw:
            if a != node or dep < at + (120 if path else 0):
                continue
            if not path and dep >= 7 * 86400:
                continue
            total_wait = waiting + dep - at
            if wait_limit is not None and total_wait > wait_limit:
                continue
            walk(
                b,
                arr,
                total_wait,
                first if path else dep,
                tuple(sorted(set(modes) | {mode})),
                path + (identifier,),
            )

    walk("A", 0, 0, 0, (), ())
    return answers


def passenger(scope: Any, table: Any, limit: int | None = None) -> Any:
    m = demo("intercity")
    return analyze_intercity_passenger(
        scope,
        m["START"],
        table,
        tuple(TransportModeBinding(mode, mode, m["PROOF"]) for mode in ("rail", "air", "road")),
        tuple(
            PhysicalService(
                r.trip_id, (r.run_id,), (PhysicalDeparture(0, r.trip_id, m["PROOF"]),), m["PROOF"]
            )
            for r in table.runs
        ),
        build_reference(m["demo_reference"](scope), scope.comparison_scope),
        config=load_passenger_config(),
        rules=load_intercity_passenger_rules(),
        frequency_start_at=m["START"],
        frequency_end_at=m["START"] + timedelta(days=7),
        resource_limit=limit,
    )


def cargo_case(width: int) -> tuple[Any, ...]:
    m = demo("delivery")
    scope, states, actions, ready = m["demo_inputs"]()
    expanded = tuple(
        replace(a, action_id=f"{a.action_id}:{i}", seconds=a.seconds + i * 37)
        for a in actions
        if a.kind == "handling"
        for i in range(width)
    )
    return m, scope, states, tuple(a for a in actions if a.kind == "movement") + expanded, ready


def cargo(case: tuple[Any, ...], limit: int | None = None) -> Any:
    m, scope, states, actions, ready = case
    return analyze_intercity_cargo(
        scope,
        m["START"],
        m["PROFILE"],
        states,
        actions,
        ready,
        config=load_freight_config(),
        rules=load_cargo_delivery_rules(),
        resource_limit=limit,
    )


def enumerate_cargo(case: tuple[Any, ...]) -> list[tuple[float, tuple[str, ...]]]:
    m, scope, states, actions, ready = case
    terminal = {s.state_id for s in states if s.stage == "completed"}
    answers = []

    def walk(state: str, at: float, first: float | None, path: tuple[str, ...]) -> None:
        if state in terminal:
            answers.append((at, path))
            return
        for a in actions:
            if (
                a.from_state_id != state
                or a.action_id in path
                or not set(a.requires_operations) <= set(path)
            ):
                continue
            if a.active is not True or a.seconds is None or a.compatibility.state != "confirmed":
                continue
            departure = (a.scheduled_at - m["START"]).total_seconds() if a.scheduled_at else at
            if departure < at:
                continue
            if a.kind == "movement" and first is None and departure >= 31 * 86400:
                continue
            if departure > at and (
                a.wait_policy is None or a.wait_policy.compatibility.state != "confirmed"
            ):
                continue
            end = departure + a.seconds
            if end > (scope.end_at - m["START"]).total_seconds():
                continue
            walk(
                a.to_state_id,
                end,
                departure if first is None and a.kind == "movement" else first,
                path + (a.action_id,),
            )

    for r in ready:
        walk(r.state_id, 0, None, ())
    return answers


def od_case(size: int) -> tuple[Any, ...]:
    m = demo("od")
    plan, _, _, context, population = m["synthetic_plan"]()
    points = tuple(
        replace(
            plan.points[0],
            point_id=str(i),
            cell_id=f"cell:{i}",
            origin_node_id=str(i),
            access_node_id=str(i),
            population=10 ** (i % 4),
        )
        for i in range(size)
    )
    plan = replace(
        plan,
        points=points,
        scope=replace(plan.scope, point_ids=frozenset(p.point_id for p in points)),
    )
    population = replace(population, point_ids=plan.scope.point_ids)
    return plan, context, population


def od(size: int) -> Any:
    plan, context, population = od_case(size)
    collector = UrbanODCollector(plan)
    for batch in plan.batches(137):
        collector.add(
            UrbanODObservation(
                t,
                UrbanODPair(
                    t.origin.point_id,
                    t.destination.point_id,
                    "reachable",
                    100
                    + 7 * int(t.origin.point_id)
                    + int(t.destination.point_id)
                    + plan.departures.index(t.departure_at),
                    True,
                    True,
                    ("proof",),
                ),
            )
            for t in batch
        )
    return calculate_urban_tau(
        context,
        plan.points,
        population,
        (collector.to_week(),),
        passenger_mode="private_car",
        timezone="UTC",
        reference_at=plan.reference_at,
        rules=load_urban_tau_rules(),
    )
