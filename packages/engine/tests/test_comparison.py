from typing import Literal, cast

import pytest
from a2_engine import (
    ComparisonContext,
    DeltaResult,
    MetricResult,
    TravelTime,
    delta_index,
    time_saved_seconds,
)

COMPARISON = ComparisonContext(True, "synthetic-fixed-inputs-check")
UNKNOWN = MetricResult(None, "unknown", "DATA_UNAVAILABLE")


@pytest.mark.parametrize("before, after, expected", [(70, 55, -15), (40, 90, 50), (50, 50, 0)])
def test_index_delta_has_the_correct_sign(before: float, after: float, expected: float) -> None:
    assert delta_index(
        MetricResult(before, "available"), MetricResult(after, "available"), comparison=COMPARISON
    ) == DeltaResult(expected, "available")


@pytest.mark.parametrize(
    "before, after, expected", [(1800, 2100, -300), (2100, 1800, 300), (0, 0, 0)]
)
def test_time_saving_has_the_correct_sign(before: float, after: float, expected: float) -> None:
    assert time_saved_seconds(
        TravelTime(before, True), TravelTime(after, True), comparison=COMPARISON
    ) == DeltaResult(expected, "available")


def test_unknown_and_incomparable_results_have_no_numeric_delta() -> None:
    known = MetricResult(70, "available")
    for before, after in [(UNKNOWN, known), (known, UNKNOWN)]:
        assert delta_index(before, after, comparison=COMPARISON) == DeltaResult(
            None, "unknown", "DATA_UNAVAILABLE"
        )
    different = ComparisonContext(False, "synthetic-different-snapshots")
    assert delta_index(known, known, comparison=different).reason_code == "INCOMPARABLE_RESULTS"
    assert (
        time_saved_seconds(
            TravelTime(0, True), TravelTime(0, True), comparison=different
        ).reason_code
        == "INCOMPARABLE_RESULTS"
    )
    for missing in [TravelTime(None, True), TravelTime(None, None)]:
        assert time_saved_seconds(
            missing, TravelTime(600, True), comparison=COMPARISON
        ) == DeltaResult(None, "unknown", "DATA_UNAVAILABLE")


def test_new_and_lost_connections_do_not_get_fictitious_travel_times() -> None:
    absent = TravelTime(None, False)
    known = TravelTime(600, True)
    assert time_saved_seconds(absent, known, comparison=COMPARISON) == DeltaResult(
        None, "not_applicable", "CONNECTION_APPEARED"
    )
    assert time_saved_seconds(known, absent, comparison=COMPARISON) == DeltaResult(
        None, "not_applicable", "CONNECTION_DISAPPEARED"
    )
    assert time_saved_seconds(absent, absent, comparison=COMPARISON).reason_code == "NO_CONNECTION"
    no_route_index = MetricResult(0, "available", "NO_FEASIBLE_ROUTE")
    assert delta_index(
        no_route_index, MetricResult(60, "available"), comparison=COMPARISON
    ) == DeltaResult(60, "available", "CONNECTION_APPEARED")
    assert delta_index(
        MetricResult(60, "available"), no_route_index, comparison=COMPARISON
    ) == DeltaResult(-60, "available", "CONNECTION_DISAPPEARED")


def test_nonnegative_metric_invariants_and_index_range_are_preserved() -> None:
    assert DeltaResult(-300, "available").value == -300
    with pytest.raises(ValueError):
        MetricResult(-300, "available")
    with pytest.raises(ValueError):
        TravelTime(-300, True)
    with pytest.raises(ValueError):
        TravelTime(0, False)
    with pytest.raises(ValueError):
        delta_index(
            MetricResult(120, "available"), MetricResult(90, "available"), comparison=COMPARISON
        )


@pytest.mark.parametrize("bad", [True, "1", float("nan"), float("inf"), -float("inf"), 10**400])
def test_invalid_signed_deltas(bad: object) -> None:
    with pytest.raises(ValueError):
        DeltaResult(cast(float, bad), "available")


@pytest.mark.parametrize(
    "value, status, reason",
    [
        (1, "unknown", "DATA_UNAVAILABLE"),
        (None, "not_applicable", None),
        (None, "partial", "DATA_UNAVAILABLE"),
    ],
)
def test_invalid_delta_states(value: float | None, status: str, reason: str | None) -> None:
    with pytest.raises(ValueError):
        DeltaResult(value, cast(Literal["available", "unknown", "not_applicable"], status), reason)


def test_comparability_confirmation_is_explicit() -> None:
    with pytest.raises(ValueError):
        ComparisonContext(True, "")
    with pytest.raises(ValueError):
        ComparisonContext(cast(bool, 1), "check")
