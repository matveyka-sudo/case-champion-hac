"""Compare already computed, externally certified comparable results."""

from dataclasses import dataclass
from math import isfinite
from typing import Literal

from a2_engine.results import MetricResult, finite_number, validate_index


@dataclass(frozen=True)
class ComparisonContext:
    """verification_id refers to caller's check of fixed inputs and allowed change."""

    comparable: bool
    verification_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.comparable, bool):
            raise ValueError("comparable must be a boolean")
        if not isinstance(self.verification_id, str) or not self.verification_id.strip():
            raise ValueError("verification_id must identify the caller's comparability check")


@dataclass(frozen=True)
class DeltaResult:
    """Signed finite delta; does not weaken MetricResult's nonnegative invariant."""

    value: float | None
    status: Literal["available", "unknown", "not_applicable"]
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("available", "unknown", "not_applicable"):
            raise ValueError("Unsupported delta status")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str) or not self.reason_code.strip()
        ):
            raise ValueError("reason_code must be a nonempty string")
        if self.status == "available":
            if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
                raise ValueError("delta must be a finite signed number")
            try:
                value = float(self.value)
            except OverflowError as exc:
                raise ValueError("delta must be finite") from exc
            if not isfinite(value):
                raise ValueError("delta must be finite")
            object.__setattr__(self, "value", value)
        elif self.value is not None or self.reason_code is None:
            raise ValueError("Unavailable deltas require value=None and a reason_code")


@dataclass(frozen=True)
class TravelTime:
    """Absence of a path has no numeric duration; unknown existence is explicit."""

    seconds: float | None
    route_exists: bool | None

    def __post_init__(self) -> None:
        if self.route_exists is not None and not isinstance(self.route_exists, bool):
            raise ValueError("route_exists must be a boolean or None")
        if self.seconds is not None:
            object.__setattr__(self, "seconds", finite_number(self.seconds, "travel seconds"))
            if self.route_exists is not True:
                raise ValueError("A known duration requires a confirmed existing route")


def delta_index(
    before: MetricResult, after: MetricResult, *, comparison: ComparisonContext
) -> DeltaResult:
    """after - before in index points; negative changes are retained."""
    validate_index(before)
    validate_index(after)
    if not isinstance(comparison, ComparisonContext):
        raise ValueError("An explicit ComparisonContext is required")
    if not comparison.comparable:
        return DeltaResult(None, "unknown", "INCOMPARABLE_RESULTS")
    if before.value is None or after.value is None:
        return DeltaResult(None, "unknown", "DATA_UNAVAILABLE")
    reason = None
    if before.reason_code == "NO_FEASIBLE_ROUTE" and after.reason_code != "NO_FEASIBLE_ROUTE":
        reason = "CONNECTION_APPEARED"
    elif before.reason_code != "NO_FEASIBLE_ROUTE" and after.reason_code == "NO_FEASIBLE_ROUTE":
        reason = "CONNECTION_DISAPPEARED"
    return DeltaResult(after.value - before.value, "available", reason)


def time_saved_seconds(
    before: TravelTime, after: TravelTime, *, comparison: ComparisonContext
) -> DeltaResult:
    """before - after; connection changes carry a reason, never an infinite delta."""
    if not isinstance(before, TravelTime) or not isinstance(after, TravelTime):
        raise ValueError("TravelTime inputs are required")
    if not isinstance(comparison, ComparisonContext):
        raise ValueError("An explicit ComparisonContext is required")
    if not comparison.comparable:
        return DeltaResult(None, "unknown", "INCOMPARABLE_RESULTS")
    if before.route_exists is None or after.route_exists is None:
        return DeltaResult(None, "unknown", "DATA_UNAVAILABLE")
    if not before.route_exists or not after.route_exists:
        if not before.route_exists and after.route_exists:
            reason = "CONNECTION_APPEARED"
        elif before.route_exists and not after.route_exists:
            reason = "CONNECTION_DISAPPEARED"
        else:
            reason = "NO_CONNECTION"
        return DeltaResult(None, "not_applicable", reason)
    if before.seconds is None or after.seconds is None:
        return DeltaResult(None, "unknown", "DATA_UNAVAILABLE")
    return DeltaResult(before.seconds - after.seconds, "available")
