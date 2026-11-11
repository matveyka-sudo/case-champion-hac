from dataclasses import replace
from typing import cast

import pytest
from a2_engine import FreightConfig, MetricResult, PipelineBasis, pipeline_utilization

BASIS = PipelineBasis("oil", "2026", "tonnes", "synthetic-pipe-a", "node-a->node-b")


@pytest.mark.parametrize(
    "volume, expected, diagnostics",
    [(120, 120, ("UTILIZATION_ABOVE_100",)), (100, 100, ()), (0, 0, ())],
)
def test_utilization_is_not_clipped(
    freight_config: FreightConfig,
    volume: float,
    expected: float,
    diagnostics: tuple[str, ...],
) -> None:
    result = pipeline_utilization(
        volume,
        100,
        volume_basis=BASIS,
        capacity_basis=BASIS,
        connection_confirmed=True,
        config=freight_config,
    )
    assert result.utilization_percent == MetricResult(expected, "available")
    assert result.diagnostics == diagnostics


@pytest.mark.parametrize(
    "field, other",
    [
        ("product", "gas"),
        ("period", "2025"),
        ("unit", "m3"),
        ("connection_id", "other-pipe"),
        ("direction", "node-b->node-a"),
    ],
)
def test_pipeline_basis_must_match(freight_config: FreightConfig, field: str, other: str) -> None:
    result = pipeline_utilization(
        120,
        100,
        volume_basis=BASIS,
        capacity_basis=replace(BASIS, **{field: other}),
        connection_confirmed=True,
        config=freight_config,
    )
    assert result.utilization_percent == MetricResult(None, "unknown", "INCOMPARABLE_PIPELINE_DATA")


def test_missing_or_unconfirmed_pipeline_data_are_not_zero(freight_config: FreightConfig) -> None:
    for v, q in [(None, 100), (0, None)]:
        result = pipeline_utilization(
            v,
            q,
            volume_basis=BASIS,
            capacity_basis=BASIS,
            connection_confirmed=True,
            config=freight_config,
        )
        assert result.utilization_percent == MetricResult(None, "unknown", "DATA_UNAVAILABLE")
    result = pipeline_utilization(
        120,
        100,
        volume_basis=BASIS,
        capacity_basis=BASIS,
        connection_confirmed=False,
        config=freight_config,
    )
    assert result.utilization_percent.reason_code == "CONNECTION_UNCONFIRMED"


@pytest.mark.parametrize("bad", [-1, True, "1", float("nan"), float("inf")])
def test_invalid_quantities_are_rejected(freight_config: FreightConfig, bad: object) -> None:
    for v, q in [(bad, 100), (120, bad)]:
        with pytest.raises(ValueError):
            pipeline_utilization(
                cast(float, v),
                cast(float, q),
                volume_basis=BASIS,
                capacity_basis=BASIS,
                connection_confirmed=True,
                config=freight_config,
            )


def test_capacity_must_be_positive_and_percentage_finite(freight_config: FreightConfig) -> None:
    with pytest.raises(ValueError):
        pipeline_utilization(
            120,
            0,
            volume_basis=BASIS,
            capacity_basis=BASIS,
            connection_confirmed=True,
            config=freight_config,
        )
    result = pipeline_utilization(
        1e308,
        1e308,
        volume_basis=BASIS,
        capacity_basis=BASIS,
        connection_confirmed=True,
        config=freight_config,
    )
    assert result.utilization_percent.value == 100
    with pytest.raises(ValueError, match="UTILIZATION_OVERFLOW"):
        pipeline_utilization(
            1e308,
            1e-308,
            volume_basis=BASIS,
            capacity_basis=BASIS,
            connection_confirmed=True,
            config=freight_config,
        )
