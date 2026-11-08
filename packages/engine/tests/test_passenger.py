from dataclasses import replace
from typing import cast

import pytest
from a2_engine import (
    MetricResult,
    PassengerChain,
    PassengerConfig,
    ReferenceTime,
    intercity_car_index,
    intercity_passenger_index,
)


@pytest.fixture
def reference(passenger_config: PassengerConfig) -> ReferenceTime:
    return ReferenceTime("synthetic-ref", 3600, passenger_config.reference_profile, "full", True)


@pytest.mark.parametrize(
    "wait, expected_wait, expected_route", [(43200, 50, 50), (0, 100, 70.71067811865476)]
)
def test_acceptance_passenger_chain(
    passenger_config: PassengerConfig,
    reference: ReferenceTime,
    wait: float,
    expected_wait: float,
    expected_route: float,
) -> None:
    result = intercity_passenger_index(
        PassengerChain("synthetic-route", 7200, wait),
        reference,
        config=passenger_config,
    )
    assert result.path_index == MetricResult(50, "available")
    assert result.waiting_index == MetricResult(expected_wait, "available")
    assert result.index.value == pytest.approx(expected_route, abs=1e-12)
    assert result.completeness == "full"
    assert result.chain.chain_id == "synthetic-route"
    assert result.reference == reference


def test_car_has_only_path_component(
    passenger_config: PassengerConfig, reference: ReferenceTime
) -> None:
    result = intercity_car_index("synthetic-car", 7200, reference, config=passenger_config)
    assert result.path_index == MetricResult(50, "available")
    assert result.completeness == "full"
    assert not hasattr(result, "waiting_index")


def test_partial_reference_preserves_numeric_value_but_not_full_rating(
    passenger_config: PassengerConfig,
    reference: ReferenceTime,
) -> None:
    reference = replace(reference, completeness="partial")
    result = intercity_passenger_index(
        PassengerChain("r", 7200, 43200), reference, config=passenger_config
    )
    assert result.index.status == "available"
    assert result.index.value == pytest.approx(50)
    assert result.completeness == "partial"
    assert result.diagnostics == ("PARTIAL_REFERENCE",)
    assert (
        intercity_car_index("car", 7200, reference, config=passenger_config).completeness
        == "partial"
    )


@pytest.mark.parametrize(
    "seconds, applicable, version, reason",
    [
        (None, True, None, "REFERENCE_UNAVAILABLE"),
        (3600, False, None, "REFERENCE_NOT_APPLICABLE"),
        (3600, True, "wrong-version", "REFERENCE_VERSION_MISMATCH"),
    ],
)
def test_unusable_reference_does_not_erase_known_wait(
    passenger_config: PassengerConfig,
    reference: ReferenceTime,
    seconds: float | None,
    applicable: bool,
    version: str | None,
    reason: str,
) -> None:
    result = intercity_passenger_index(
        PassengerChain("r", 7200, 43200),
        replace(
            reference,
            seconds=seconds,
            applicability_confirmed=applicable,
            profile_version=version if version is not None else reference.profile_version,
        ),
        config=passenger_config,
    )
    assert result.index == MetricResult(None, "unknown", reason)
    assert result.path_index == result.index
    assert result.waiting_index.value == 50
    assert result.completeness == "partial"


def test_reference_inconsistency_is_capped_and_diagnosed(
    passenger_config: PassengerConfig,
    reference: ReferenceTime,
) -> None:
    ref = replace(reference, seconds=9000)
    result = intercity_passenger_index(PassengerChain("r", 7200, 0), ref, config=passenger_config)
    assert result.path_index.value == 100
    assert result.index.value == 100
    assert result.diagnostics == ("REFERENCE_INCONSISTENCY",)
    assert (
        intercity_car_index("car", 7200, ref, config=passenger_config).diagnostics
        == result.diagnostics
    )
    assert ref.seconds == 9000  # No automatic adjustment of the accepted reference.


def test_missing_chain_component_is_not_zero(
    passenger_config: PassengerConfig, reference: ReferenceTime
) -> None:
    result = intercity_passenger_index(
        PassengerChain("r", 7200, None), reference, config=passenger_config
    )
    assert result.path_index.value == 50
    assert result.waiting_index.value is None
    assert result.index == MetricResult(None, "unknown", "DATA_UNAVAILABLE")
    assert result.completeness == "partial"


def test_zero_path_is_invalid_even_with_unknown_reference(
    passenger_config: PassengerConfig,
    reference: ReferenceTime,
) -> None:
    with pytest.raises(ValueError, match="INVALID_PATH_TIME"):
        PassengerChain("r", 0, 0)
    with pytest.raises(ValueError, match="INVALID_PATH_TIME"):
        intercity_car_index("r", 0, replace(reference, seconds=None), config=passenger_config)


@pytest.mark.parametrize("bad", [-1, True, "1", float("nan"), float("inf")])
def test_invalid_passenger_numbers_are_rejected(reference: ReferenceTime, bad: object) -> None:
    with pytest.raises(ValueError):
        PassengerChain("r", cast(float, bad), 0)
    with pytest.raises(ValueError):
        PassengerChain("r", 7200, cast(float, bad))
    with pytest.raises(ValueError):
        replace(reference, seconds=cast(float, bad))


def test_tiny_route_components_do_not_underflow_their_geometric_mean(
    passenger_config: PassengerConfig,
    reference: ReferenceTime,
) -> None:
    result = intercity_passenger_index(
        PassengerChain("tiny", 1e200, 43_200_000),
        replace(reference, seconds=1),
        config=passenger_config,
    )
    assert result.path_index.value == pytest.approx(1e-198, rel=1e-12, abs=0)
    # Both positive components have a product below floating-point range.
    assert result.path_index.value is not None and result.waiting_index.value is not None
    assert result.waiting_index.value == pytest.approx(9.332636185032189e-300, rel=1e-12, abs=0)
    assert result.path_index.value * result.waiting_index.value == 0
    assert result.index.value is not None and result.index.value > 0
