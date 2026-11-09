"""Directed end-to-end connections and independent nullable quantity states."""

import importlib.util
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from a2_engine.pipeline.analysis import analyze_pipeline_connections
from a2_engine.shared.config import load_freight_config

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/synthetic_pipeline.py"
spec = importlib.util.spec_from_file_location("pipeline_demo", EXAMPLE)
assert spec and spec.loader
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_oil_utilization_120_with_diagnostics() -> None:
    result = m.run_demo()
    assert result.state == "connected"
    assert result.connections[0].utilization.utilization_percent.value == 120


def test_reverse_transit_and_other_product_do_not_create_connections() -> None:
    scope, observation = m.demo_inputs()
    for changed in (
        replace(
            scope,
            origin_territory_id="territory-B",
            destination_territory_id="territory-A",
            origin_node_ids=("B",),
            destination_node_ids=("A",),
        ),
        replace(scope, origin_territory_id="transit-C", origin_node_ids=("C",)),
        replace(scope, product="gas"),
    ):
        result = analyze_pipeline_connections(changed, (observation,), config=load_freight_config())
        assert result.state == "confirmed_absent" and result.connections == ()
        assert result.excluded_connection_ids == ("connection",)


def test_unknown_loading_does_not_erase_confirmed_connection_and_zero_volume_keeps_it() -> None:
    scope, observation = m.demo_inputs()
    for value, expected in ((None, None), (0, 0)):
        record = replace(observation, volume=replace(observation.volume, value=value))
        result = analyze_pipeline_connections(scope, (record,), config=load_freight_config())
        assert result.state == "connected"
        assert result.connections[0].utilization.utilization_percent.value == expected


@pytest.mark.parametrize(
    "field,value",
    [
        ("period", "2025"),
        ("unit", "cubic-metres"),
        ("direction", "B->A"),
        ("connection_id", "whole-system"),
    ],
)
def test_incomparable_quantity_bases(field: str, value: str) -> None:
    scope, observation = m.demo_inputs()
    record = replace(
        observation,
        volume=replace(
            observation.volume, basis=replace(observation.volume.basis, **{field: value})
        ),
    )
    result = analyze_pipeline_connections(scope, (record,), config=load_freight_config())
    metric = result.connections[0].utilization.utilization_percent
    assert metric.value is None and metric.reason_code == "INCOMPARABLE_PIPELINE_DATA"


def test_two_matching_but_wrong_system_bases_and_wrong_real_period_rejected() -> None:
    scope, observation = m.demo_inputs()
    basis = replace(observation.volume.basis, connection_id="system-total")
    for volume, capacity in (
        (replace(observation.volume, basis=basis), replace(observation.capacity, basis=basis)),
        (
            replace(observation.volume, period_start_at=m.START - timedelta(days=365)),
            observation.capacity,
        ),
        (
            replace(observation.volume, context=replace(m.CONTEXT, snapshot_id="other")),
            observation.capacity,
        ),
    ):
        result = analyze_pipeline_connections(
            scope,
            (replace(observation, volume=volume, capacity=capacity),),
            config=load_freight_config(),
        )
        assert (
            result.connections[0].utilization.utilization_percent.reason_code
            == "INCOMPARABLE_PIPELINE_DATA"
        )


def test_empty_catalogue_and_unknown_endpoints_not_confirmed_absence() -> None:
    scope, observation = m.demo_inputs()
    assert (
        analyze_pipeline_connections(
            replace(scope, coverage_complete=False), (), config=load_freight_config()
        ).state
        == "unknown"
    )
    assert (
        analyze_pipeline_connections(scope, (), config=load_freight_config()).state
        == "confirmed_absent"
    )
    unknown = replace(
        observation,
        connection=replace(
            observation.connection, origin=replace(observation.connection.origin, state="unknown")
        ),
    )
    assert (
        analyze_pipeline_connections(scope, (unknown,), config=load_freight_config()).state
        == "unknown"
    )
    forbidden = replace(observation, connection=replace(observation.connection, state="forbidden"))
    assert (
        analyze_pipeline_connections(scope, (forbidden,), config=load_freight_config()).state
        == "confirmed_absent"
    )


def test_segments_do_not_compose_or_average_their_utilization() -> None:
    scope, observation = m.demo_inputs()
    first = replace(
        observation.connection,
        connection_id="AX",
        destination=replace(
            observation.connection.destination, node_id="X", territory_id="territory-X"
        ),
    )
    second = replace(
        observation.connection,
        connection_id="XB",
        origin=replace(observation.connection.origin, node_id="X", territory_id="territory-X"),
    )
    records = (replace(observation, connection=first), replace(observation, connection=second))
    result = analyze_pipeline_connections(scope, records, config=load_freight_config())
    assert result.connections == () and "NO_CONNECTION_COMPOSITION" in result.limitations
    # Two independent accepted A->B connections retain separate percentages.
    records = tuple(
        replace(
            observation,
            connection=replace(observation.connection, connection_id=f"c:{i}"),
            volume=replace(
                observation.volume,
                value=value,
                basis=replace(observation.volume.basis, connection_id=f"c:{i}"),
            ),
            capacity=replace(
                observation.capacity,
                basis=replace(observation.capacity.basis, connection_id=f"c:{i}"),
            ),
        )
        for i, value in enumerate((50, 80))
    )
    result = analyze_pipeline_connections(scope, records, config=load_freight_config())
    assert [r.utilization.utilization_percent.value for r in result.connections] == [50, 80]
    assert "NO_AGGREGATE_UTILIZATION" in result.limitations
    assert result == analyze_pipeline_connections(
        scope, tuple(reversed(records)), config=load_freight_config()
    )


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True])
def test_invalid_quantities(value: Any) -> None:
    _, observation = m.demo_inputs()
    with pytest.raises(ValueError):
        replace(observation.volume, value=value)


def test_nonpositive_capacity_wrong_versions_and_outside_validity() -> None:
    scope, observation = m.demo_inputs()
    with pytest.raises(ValueError):
        analyze_pipeline_connections(
            scope,
            (replace(observation, capacity=replace(observation.capacity, value=0)),),
            config=load_freight_config(),
        )
    with pytest.raises(ValueError):
        analyze_pipeline_connections(
            replace(scope, context=replace(m.CONTEXT, graph_version="old")),
            (observation,),
            config=load_freight_config(),
        )
    result = analyze_pipeline_connections(
        replace(scope, period_end_at=m.END + timedelta(days=1)),
        (observation,),
        config=load_freight_config(),
    )
    assert result.state == "unknown"
