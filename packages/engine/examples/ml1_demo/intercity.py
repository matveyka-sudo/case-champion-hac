"""Two-leg directional passenger chains and a shared reference per direction."""

from a2_engine import (
    IntercityAnalysisScope,
    ReferenceCalibration,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNetwork,
    ReferenceScope,
    ReferenceState,
    build_reference,
    load_intercity_passenger_rules,
    load_passenger_config,
)
from a2_engine.routing.reference.models import ReferenceOperation
from a2_engine.routing.timetable_models import TimetableTransfer
from a2_engine.scenario.models import (
    IntercityScenarioInput,
    ScenarioBaseline,
    ScenarioInputs,
    ScenarioOperation,
)
from a2_engine.scenario.overlay import execution_hash

from ml1_demo.model import CONFIG_PATH, DAY, PROOF, START, VERSION, City, clock, dataset, run


def passenger_inputs(origin: City, destination: City) -> ScenarioInputs:
    n = 3 * origin.number + destination.number
    snapshot = f"{VERSION}:rail:{origin.id}:{destination.id}"
    a, x, b = f"{origin.id}:station", "transfer-station", f"{destination.id}:station"
    dep = 1800 + 120 * n
    arrive_x = dep + 3600 + 300 * n
    depart_x = arrive_x + 2400 + 60 * n
    arrive_b = depart_x + 5400 + 420 * n
    ds = dataset(
        snapshot,
        (
            run(snapshot, "feeder", ((a, dep), (x, arrive_x)), "train"),
            run(snapshot, "onward", ((x, depart_x), (b, arrive_b)), "train"),
            run(snapshot, "spare", ((x, depart_x + 7200), (b, arrive_b + 7200)), "train"),
        ),
        (TimetableTransfer("rail-transfer", x, x, 600, True, PROOF),),
        mode="rail",
    )
    cfg = load_passenger_config(CONFIG_PATH)
    network = ReferenceNetwork(
        ds.timetable.scope.graph_version,
        snapshot,
        f"{snapshot}:accepted",
        f"{snapshot}:applicability",
        ds.timetable.scope.transfer_version,
        cfg.methodology_version,
        cfg.reference_profile,
        PROOF,
    )
    ref_scope = ReferenceScope(
        network, origin.id, destination.id, (a,), (b,), ("rail",), "city_pair_best_node"
    )
    states = (
        ReferenceState("a-dep", a, "departure", "rail", "platform-A"),
        ReferenceState("x-arr", x, "arrival", "rail", "platform-X1"),
        ReferenceState("x-dep", x, "departure", "rail", "platform-X2"),
        ReferenceState("b-arr", b, "arrival", "rail", "platform-B"),
    )
    arcs = tuple(
        ReferenceEdge(
            arc_id,
            source,
            target,
            (
                ReferenceCalibration(
                    f"sample:{arc_id}",
                    arc_id,
                    source,
                    target,
                    network,
                    seconds,
                    "running_time",
                    PROOF,
                ),
            ),
            PROOF,
            compatible=True,
        )
        for arc_id, source, target, seconds in (
            ("a-x", "a-dep", "x-arr", 3300 + 300 * n),
            ("x-b", "x-dep", "b-arr", 4800 + 420 * n),
        )
    )
    graph = ReferenceGraph(
        network,
        states,
        arcs,
        (
            ReferenceOperation(
                "x-transfer", "x-arr", "x-dep", network, 600, "transfer", PROOF, compatible=True
            ),
        ),
        "full",
        PROOF,
    )
    job = IntercityScenarioInput(
        snapshot,
        ds.network_id,
        IntercityAnalysisScope(ref_scope, ("rail",), True, PROOF),
        START,
        build_reference(graph, ref_scope),
        cfg,
        load_intercity_passenger_rules(CONFIG_PATH),
        START,
        clock(7 * 86400),
    )
    return ScenarioInputs(timetables=(ds,), intercity=(job,))


def passenger_operation(baseline: ScenarioBaseline, *, spare: bool = False) -> ScenarioOperation:
    ds = baseline.inputs.timetables[0]
    runs = {r.trip_id: r for r in ds.timetable.runs}
    source = runs["spare" if spare else "onward"]
    return ScenarioOperation(
        baseline.baseline_id,
        f"{ds.network_id}:coordinate:{source.trip_id}",
        "coordinate_transfer",
        "timetable",
        ds.network_id,
        source.run_id,
        DAY,
        "0",
        execution_hash(baseline, "timetable", ds.network_id, source.run_id),
        -1200,
        runs["feeder"].run_id,
        "1",
        PROOF,
    )
