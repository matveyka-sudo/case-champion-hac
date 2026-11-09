"""Shared exponential scoring with explicit seconds and missing-data states."""

from decimal import Decimal, localcontext

from a2_engine.shared.results import MetricResult, finite_number


def category_index(
    travel_seconds: float | None,
    h_seconds: float | None,
    *,
    confirmed_no_route: bool = False,
) -> MetricResult:
    """Score a supplied minimum travel time, or explicitly proven unreachability.

    The caller alone certifies complete coverage and the search window for
    confirmed_no_route. Missing times/provider failures are never such proof.
    """
    if not isinstance(confirmed_no_route, bool):
        raise ValueError("confirmed_no_route must be a boolean")
    travel = None if travel_seconds is None else finite_number(travel_seconds, "travel_seconds")
    half_life = None if h_seconds is None else finite_number(h_seconds, "h_seconds", positive=True)
    if confirmed_no_route:
        if travel is not None:
            raise ValueError("A travel time contradicts confirmed_no_route")
        return MetricResult(0.0, "available", "NO_FEASIBLE_ROUTE")
    if travel is None:
        return MetricResult(None, "unknown", "DATA_UNAVAILABLE")
    if half_life is None:
        return MetricResult(None, "unknown", "H_UNAVAILABLE")
    exponent = -travel / half_life
    if -1100 < exponent < -1000:
        # Keep the scale factor inside the high precision calculation: the
        # unscaled power may round to zero while the percentage is representable.
        with localcontext() as context:
            context.prec = 80
            value = float(Decimal(100) * Decimal(2) ** Decimal.from_float(exponent))
    else:
        value = 100.0 * 2.0**exponent
    return MetricResult(value, "available")
