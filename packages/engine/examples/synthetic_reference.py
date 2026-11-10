"""Synthetic accepted edges → computed common A → unchanged intercity formulas."""

from a2_engine import (
    CarResult,
    PassengerChain,
    PassengerResult,
    ReferenceCalibration,
    ReferenceComparison,
    ReferenceEdge,
    ReferenceGraph,
    ReferenceNetwork,
    ReferenceOperation,
    ReferenceScope,
    ReferenceSearchResult,
    ReferenceState,
    build_reference,
    intercity_car_index,
    intercity_passenger_index,
    load_passenger_config,
    reference_time_from_result,
)


def demo_inputs() -> tuple[ReferenceGraph, ReferenceScope]:
    config = load_passenger_config()
    network = ReferenceNetwork(
        "synthetic-graph",
        "synthetic-snapshot",
        "synthetic-acceptance",
        "synthetic-scope",
        "compat-v1",
        config.methodology_version,
        config.reference_profile,
        ("ev:network",),
    )
    states = (
        ReferenceState("A:rail", "A", "departure", "rail", "platform-A"),
        ReferenceState("X:rail", "X", "arrival", "rail", "rail-arrival"),
        ReferenceState("X:air", "X", "departure", "air", "air-departure"),
        ReferenceState("B:air", "B", "arrival", "air", "platform-B"),
        ReferenceState("A:air", "A", "departure", "air", "direct-departure"),
    )
    edges = tuple(
        ReferenceEdge(
            name,
            start,
            end,
            (
                ReferenceCalibration(
                    f"cal:{name}",
                    name,
                    start,
                    end,
                    network,
                    seconds,
                    "running_time",
                    (f"ev:{name}",),
                ),
            ),
            (f"ev:edge:{name}",),
            compatible=True,
        )
        for name, start, end, seconds in (
            ("rail", "A:rail", "X:rail", 1800.0),
            ("air", "X:air", "B:air", 1500.0),
            ("direct", "A:air", "B:air", 5000.0),
        )
    )
    transfer = ReferenceOperation(
        "transfer",
        "X:rail",
        "X:air",
        network,
        300,
        "mandatory_internal_transfer",
        ("ev:transfer",),
        compatible=True,
    )
    graph = ReferenceGraph(network, states, edges, (transfer,), "full", ("ev:coverage",))
    scope = ReferenceScope(
        network, "city-A", "city-B", ("A",), ("B",), ("rail", "air"), "city_pair_best_node"
    )
    return graph, scope


def run_demo() -> tuple[ReferenceSearchResult, PassengerResult, CarResult]:
    graph, scope = demo_inputs()
    result = build_reference(graph, scope)
    config = load_passenger_config()
    scalar = reference_time_from_result(
        result, ReferenceComparison(scope, "A", "B", "live"), config=config
    )
    passenger = intercity_passenger_index(
        PassengerChain("synthetic-chain", 7200, 43200), scalar, config=config
    )
    car = intercity_car_index("synthetic-car", 7200, scalar, config=config)
    assert result.seconds == 3600 and result.full_ranking_eligible
    assert passenger.path_index.value == passenger.waiting_index.value == car.path_index.value == 50
    assert passenger.index.value is not None and abs(passenger.index.value - 50) < 1e-10
    return result, passenger, car


if __name__ == "__main__":
    reference, passenger, car = run_demo()
    print(
        {
            "synthetic": True,
            "A_seconds": reference.seconds,
            "edges": reference.path.edge_ids if reference.path else (),
            "passenger_index": passenger.index.value,
            "car_index": car.path_index.value,
        }
    )
