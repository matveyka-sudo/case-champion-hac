import copy
import math
from collections.abc import Mapping
from typing import Literal, cast

import pytest
from a2_engine import (
    AreaHBand,
    MetricResult,
    UrbanConfig,
    category_index,
    is_low_accessibility,
    point_index,
    point_indices_by_mode,
    select_h_seconds,
    territory_index,
)

UNKNOWN = MetricResult(None, "unknown", "DATA_UNAVAILABLE")
NOT_APPLICABLE = MetricResult(None, "not_applicable", "NO_APPLICABLE_METRIC")
INVALID_NUMBERS = [-1, "12", True, [], float("nan"), float("inf"), -float("inf"), 10**400]


@pytest.mark.parametrize("travel, expected", [(0, 100), (1200, 50), (2400, 25)])
def test_acceptance_category_index(travel: float, expected: float) -> None:
    assert category_index(travel, 1200) == MetricResult(expected, "available")


@pytest.mark.parametrize(
    "area, seconds",
    [(100, 1200), (100.01, 1800), (300, 1800), (300.01, 2400), (1000, 2400), (1000.01, 3600)],
)
def test_acceptance_area_boundaries(config: UrbanConfig, area: float, seconds: float) -> None:
    assert select_h_seconds(area, bands=config.area_h_bands) == MetricResult(seconds, "available")


def test_unknown_area_does_not_get_default(config: UrbanConfig) -> None:
    h = select_h_seconds(None, bands=config.area_h_bands)
    assert h == MetricResult(None, "unknown", "AREA_UNAVAILABLE")
    assert category_index(100, h.value) == MetricResult(None, "unknown", "H_UNAVAILABLE")


@pytest.mark.parametrize("bad", INVALID_NUMBERS + [0])
def test_invalid_area(config: UrbanConfig, bad: object) -> None:
    with pytest.raises(ValueError):
        select_h_seconds(cast(float, bad), bands=config.area_h_bands)


@pytest.mark.parametrize("value, expected", [(49.999, True), (50, False), (50.001, False)])
def test_low_accessibility_is_strict(config: UrbanConfig, value: float, expected: bool) -> None:
    assert (
        is_low_accessibility(
            MetricResult(value, "available"), threshold=config.low_accessibility_below
        )
        is expected
    )


@pytest.mark.parametrize("result", [UNKNOWN, NOT_APPLICABLE])
def test_unavailable_low_classification(config: UrbanConfig, result: MetricResult) -> None:
    assert is_low_accessibility(result, threshold=config.low_accessibility_below) is None


def test_confirmed_no_route_is_distinct_from_missing_data() -> None:
    assert category_index(None, 1200, confirmed_no_route=True) == MetricResult(
        0, "available", "NO_FEASIBLE_ROUTE"
    )
    assert category_index(None, 1200) == UNKNOWN
    # H is unnecessary for an explicitly proven zero, but is never invented.
    assert category_index(None, None, confirmed_no_route=True).value == 0
    with pytest.raises(ValueError):
        category_index(0, 1200, confirmed_no_route=True)
    with pytest.raises(ValueError):
        category_index(None, 1200, confirmed_no_route=cast(bool, 1))


@pytest.mark.parametrize("bad", INVALID_NUMBERS)
def test_invalid_travel_time(bad: object) -> None:
    with pytest.raises(ValueError):
        category_index(cast(float, bad), 1200)


@pytest.mark.parametrize("bad", INVALID_NUMBERS + [0])
@pytest.mark.parametrize("travel, no_route", [(10, False), (None, False), (None, True)])
def test_invalid_h_is_rejected_even_when_time_missing(
    bad: object, travel: float | None, no_route: bool
) -> None:
    with pytest.raises(ValueError):
        category_index(travel, cast(float, bad), confirmed_no_route=no_route)


@pytest.mark.parametrize("bad", INVALID_NUMBERS + [100.1])
def test_invalid_threshold(bad: object) -> None:
    with pytest.raises(ValueError):
        is_low_accessibility(UNKNOWN, threshold=cast(float, bad))


def test_acceptance_point_priority_weighting(config: UrbanConfig) -> None:
    assert point_index(
        {"school": MetricResult(100, "available"), "airport": MetricResult(0, "available")},
        {"school": "low", "airport": "high"},
        config=config,
    ) == MetricResult(25, "available")


@pytest.mark.parametrize("missing", [UNKNOWN, NOT_APPLICABLE])
def test_selected_unavailable_category_blocks_point(
    config: UrbanConfig, missing: MetricResult
) -> None:
    assert point_index(
        {"school": MetricResult(100, "available"), "airport": missing},
        {"school": "low", "airport": "high"},
        config=config,
    ) == MetricResult(None, "unknown", "CATEGORY_UNAVAILABLE")


def test_unselected_unknown_category_does_not_participate(config: UrbanConfig) -> None:
    assert (
        point_index(
            {"school": MetricResult(100, "available"), "airport": UNKNOWN},
            {"school": "low"},
            config=config,
        ).value
        == 100
    )


@pytest.mark.parametrize(
    "indices, selection",
    [
        ({}, {}),
        ({"school": UNKNOWN}, {"school": "very_high"}),
        ({"school": UNKNOWN}, {"school": cast(str, None)}),
        ({"school": UNKNOWN}, {"school": cast(str, [])}),
        ({"other": UNKNOWN}, {"other": "low"}),
        ({"school": UNKNOWN}, {"airport": "low"}),
        ({"school": UNKNOWN, "other": UNKNOWN}, {"school": "low"}),
        ({"school": MetricResult(101, "available")}, {"school": "low"}),
    ],
)
def test_invalid_point_inputs(
    config: UrbanConfig, indices: Mapping[str, MetricResult], selection: Mapping[str, str]
) -> None:
    with pytest.raises(ValueError):
        point_index(indices, selection, config=config)


def test_modes_are_computed_separately(config: UrbanConfig) -> None:
    actual = point_indices_by_mode(
        {
            "walk": {"school": category_index(2400, 1200)},
            "public_transport": {"school": UNKNOWN},
            "private_car": {"school": category_index(1200, 1200)},
        },
        {"school": "medium"},
        config=config,
    )
    assert actual == {
        "walk": MetricResult(25, "available"),
        "public_transport": MetricResult(None, "unknown", "CATEGORY_UNAVAILABLE"),
        "private_car": MetricResult(50, "available"),
    }
    for modes in ({}, {"cargo": {"school": UNKNOWN}}):
        with pytest.raises(ValueError):
            point_indices_by_mode(modes, {"school": "low"}, config=config)


def test_acceptance_territory_population_weighting() -> None:
    assert territory_index(
        {"a": MetricResult(100, "available"), "b": MetricResult(0, "available")},
        {"a": 90, "b": 10},
    ) == MetricResult(90, "available")


def test_acceptance_population_of_source_cell_and_district_shares() -> None:
    # Synthetic cells: a point moved into a mall still represents 200 residents.
    indices = {"mall_cell": MetricResult(100, "available"), "b": MetricResult(0, "available")}
    assert territory_index(indices, {"mall_cell": 200, "b": 50}).value == 80
    # Prepared district shares retain the same original population; no mean of district means.
    shares = {"a_part1": indices["mall_cell"], "a_part2": indices["mall_cell"], "b": indices["b"]}
    assert territory_index(shares, {"a_part1": 40, "a_part2": 160, "b": 50}).value == 80


@pytest.mark.parametrize("missing", [UNKNOWN, NOT_APPLICABLE])
def test_unknown_positive_population_index_blocks_territory(missing: MetricResult) -> None:
    assert territory_index(
        {"a": MetricResult(100, "available"), "b": missing}, {"a": 90, "b": 10}
    ) == MetricResult(None, "unknown", "POINT_INDEX_UNAVAILABLE")


@pytest.mark.parametrize("index", [UNKNOWN, MetricResult(0, "available")])
def test_unknown_population_is_not_zero_or_one(index: MetricResult) -> None:
    assert territory_index(
        {"a": MetricResult(100, "available"), "b": index}, {"a": 90, "b": None}
    ) == MetricResult(None, "unknown", "POPULATION_UNAVAILABLE")


@pytest.mark.parametrize("missing", [UNKNOWN, NOT_APPLICABLE])
def test_unknown_index_at_known_zero_population_is_ignored(missing: MetricResult) -> None:
    assert territory_index(
        {"a": MetricResult(75, "available"), "b": missing}, {"a": 90, "b": 0}
    ) == MetricResult(75, "available")


def test_zero_weight_and_empty_territory_are_not_applicable() -> None:
    assert territory_index({"a": UNKNOWN}, {"a": 0}) == MetricResult(
        None, "not_applicable", "ZERO_WEIGHT"
    )
    assert territory_index({}, {}) == MetricResult(None, "not_applicable", "ZERO_WEIGHT")


@pytest.mark.parametrize("bad", INVALID_NUMBERS)
def test_invalid_population_is_not_hidden_by_unknown(bad: object) -> None:
    with pytest.raises(ValueError):
        territory_index({"a": UNKNOWN, "b": UNKNOWN}, {"a": None, "b": cast(float, bad)})


def test_inconsistent_population_ids_are_invalid() -> None:
    with pytest.raises(ValueError):
        territory_index({"a": UNKNOWN}, {"b": 10})
    with pytest.raises(ValueError):
        territory_index({"": UNKNOWN}, {"": 10})
    with pytest.raises(ValueError):
        territory_index({"a": MetricResult(101, "available")}, {"a": 10})


def test_finite_extreme_inputs_do_not_overflow_outputs() -> None:
    assert category_index(1e308, 1e-308).value == 0
    assert (
        territory_index(
            {"a": MetricResult(100, "available"), "b": MetricResult(0, "available")},
            {"a": 1e308, "b": 1e308},
        ).value
        == 50
    )


def test_fractional_weights_preserve_the_index_range() -> None:
    assert territory_index(
        {"a": MetricResult(100, "available"), "b": MetricResult(100, "available")},
        {"a": 0.1, "b": 0.7},
    ) == MetricResult(100, "available")


def test_no_intermediate_rounding_and_order_invariance(config: UrbanConfig) -> None:
    exact = category_index(1200, 1800)
    assert exact.value == pytest.approx(62.99605249474366, abs=1e-12)
    assert exact.value != round(cast(float, exact.value), 1)
    indices = {
        "a": exact,
        "b": MetricResult(37.1, "available"),
        "c": MetricResult(100, "available"),
    }
    population = {"a": 17.5, "b": 0.25, "c": 3.1}
    assert territory_index(indices, population) == territory_index(
        dict(reversed(list(indices.items()))), dict(reversed(list(population.items())))
    )


def test_computation_does_not_mutate_inputs(config: UrbanConfig) -> None:
    indices = {"school": category_index(600, 1200), "airport": UNKNOWN}
    selection = {"school": "medium"}
    populations = {"a": 12.5, "b": 0}
    points = {"a": indices["school"], "b": UNKNOWN}
    modes = {"walk": indices}
    before = copy.deepcopy((indices, selection, populations, points, modes))
    config_before = (
        config.methodology_version,
        config.area_h_bands,
        dict(config.priority_weights),
        config.categories,
        config.modes,
        config.low_accessibility_below,
    )
    point_index(indices, selection, config=config)
    point_indices_by_mode(modes, selection, config=config)
    territory_index(points, populations)
    select_h_seconds(100, bands=config.area_h_bands)
    assert before == (indices, selection, populations, points, modes)
    assert config_before == (
        config.methodology_version,
        config.area_h_bands,
        dict(config.priority_weights),
        config.categories,
        config.modes,
        config.low_accessibility_below,
    )


@pytest.mark.parametrize("bad", INVALID_NUMBERS + [None])
def test_available_result_requires_valid_number(bad: object) -> None:
    with pytest.raises(ValueError):
        MetricResult(cast(float, bad), "available")


@pytest.mark.parametrize(
    "value, status, reason",
    [
        (1, "unknown", "DATA_UNAVAILABLE"),
        (None, "unknown", None),
        (None, "not_applicable", ""),
        (None, "other", "DATA_UNAVAILABLE"),
        (1, "available", "NO_FEASIBLE_ROUTE"),
        (0, "available", "ZERO_WEIGHT"),
    ],
)
def test_inconsistent_result_states(value: float | None, status: str, reason: str | None) -> None:
    with pytest.raises(ValueError):
        MetricResult(value, cast(Literal["available", "unknown", "not_applicable"], status), reason)


def test_band_input_is_explicit_and_not_a_runtime_default() -> None:
    custom = (AreaHBand(10, 600), AreaHBand(None, 3000))
    assert select_h_seconds(10, bands=custom).value == 600
    assert select_h_seconds(11, bands=custom).value == 3000
    with pytest.raises(ValueError):
        select_h_seconds(None, bands=[])
    assert math.isfinite(cast(float, category_index(3, 7).value))
