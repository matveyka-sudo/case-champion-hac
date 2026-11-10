"""Computed reference A + actual timetable search: later arrival, better score."""

from datetime import UTC, date, datetime, timedelta

from a2_engine import (
    IntercityAnalysisScope,
    IntercityPassengerAnalysis,
    PhysicalDeparture,
    PhysicalService,
    ReferenceCalibration,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNetwork,
    ReferenceScope,
    ReferenceState,
    Timetable,
    TimetableEvent,
    TimetableRun,
    TimetableScope,
    TransportModeBinding,
    analyze_intercity_passenger,
    build_reference,
    load_intercity_passenger_rules,
    load_passenger_config,
)

PROOF = ("synthetic-evidence",)
START = datetime(2026, 11, 9, tzinfo=UTC)


def demo_inputs() -> tuple[IntercityAnalysisScope, Timetable]:
    network = ReferenceNetwork(
        "graph",
        "snapshot",
        "accepted",
        "scope",
        "transfers",
        "a2-methodology-v3",
        "edge-calibrated-v1",
        PROOF,
    )
    common = ReferenceScope(
        network, "city-A", "city-B", ("A",), ("B",), ("rail", "air", "road"), "city_pair_best_node"
    )
    scope = IntercityAnalysisScope(common, ("rail", "air", "road"), True, PROOF)
    runs = tuple(
        TimetableRun(
            "snapshot",
            "synthetic",
            name,
            date(2026, 11, 9),
            "UTC",
            "train",
            (
                TimetableEvent(
                    0,
                    "A",
                    START + timedelta(seconds=depart),
                    START + timedelta(seconds=depart),
                    True,
                    True,
                    PROOF,
                ),
                TimetableEvent(
                    1,
                    "B",
                    START + timedelta(seconds=arrive),
                    START + timedelta(seconds=arrive),
                    True,
                    True,
                    PROOF,
                ),
            ),
            True,
            PROOF,
        )
        for name, depart, arrive in (("R1", 0, 3600), ("R2", 3600, 5400))
    )
    ts = TimetableScope(
        "snapshot",
        "graph",
        "calendar",
        "timetable",
        "footpaths",
        "transfers",
        "UTC",
        START,
        START + timedelta(days=10),
        frozenset({"A", "B"}),
        True,
        True,
        True,
        True,
        True,
        PROOF,
    )
    return scope, Timetable(ts, runs, (), ())


def demo_reference(scope: IntercityAnalysisScope, seconds: float = 1800) -> ReferenceGraph:
    network = scope.comparison_scope.network
    states = (
        ReferenceState("dep", "A", "departure", "rail", "platform-A"),
        ReferenceState("arr", "B", "arrival", "rail", "platform-B"),
    )
    sample = ReferenceCalibration(
        "calibration", "edge", "dep", "arr", network, seconds, "running_time", PROOF
    )
    edge = ReferenceEdge("edge", "dep", "arr", (sample,), PROOF, compatible=True)
    return ReferenceGraph(network, states, (edge,), (), "full", PROOF)


def run_demo() -> IntercityPassengerAnalysis:
    scope, timetable = demo_inputs()
    reference = build_reference(demo_reference(scope), scope.comparison_scope)
    services = tuple(
        PhysicalService(
            run.trip_id,
            (run.run_id,),
            (PhysicalDeparture(0, f"departure:{run.trip_id}", PROOF),),
            PROOF,
        )
        for run in timetable.runs
    )
    result = analyze_intercity_passenger(
        scope,
        START,
        timetable,
        (TransportModeBinding("train", "rail", PROOF),),
        services,
        reference,
        config=load_passenger_config(),
        rules=load_intercity_passenger_rules(),
        frequency_start_at=START,
        frequency_end_at=START + timedelta(days=7),
    )
    assert result.public.best_known is not None
    assert result.public.best_known.path_seconds == 1800
    assert result.public.best_known.initial_wait_seconds == 3600
    assert result.public.best_known.evaluation.index.value is not None
    assert abs(result.public.best_known.evaluation.index.value - 97.15319411536) < 1e-9
    assert result.frequency[-1].count == 2
    return result


if __name__ == "__main__":
    result = run_demo()
    print(
        "SYNTHETIC intercity:",
        [
            (c.initial_wait_seconds, c.path_seconds, c.evaluation.index.value)
            for c in result.candidates
        ],
        "frequency=",
        result.frequency[-1].count,
        "optimum_proven=",
        result.public.optimum_proven,
    )
