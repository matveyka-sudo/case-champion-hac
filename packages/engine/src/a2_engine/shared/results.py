"""Internal numeric results, to be adapted to BACKEND's canonical DTOs."""

from dataclasses import dataclass
from math import isfinite
from typing import Literal


def finite_number(value: object, name: str, *, positive: bool = False) -> float:
    """Reject missing, boolean, nonnumeric, negative and nonfinite inputs."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not isfinite(number) or number < 0 or (positive and number == 0):
        condition = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be finite and {condition}")
    return number


@dataclass(frozen=True)
class MetricResult:
    """A value in the function's documented units; never a public Score DTO."""

    value: float | None
    status: Literal["available", "unknown", "not_applicable"]
    reason_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("available", "unknown", "not_applicable"):
            raise ValueError("Unsupported metric status")
        if self.reason_code is not None and (
            not isinstance(self.reason_code, str) or not self.reason_code.strip()
        ):
            raise ValueError("reason_code must be a nonempty string")
        if self.status == "available":
            object.__setattr__(self, "value", finite_number(self.value, "value"))
        elif self.value is not None or self.reason_code is None:
            raise ValueError("Unavailable metrics require value=None and a reason_code")
        if self.reason_code == "NO_FEASIBLE_ROUTE" and (
            self.status != "available" or self.value != 0
        ):
            raise ValueError("NO_FEASIBLE_ROUTE requires an available zero")
        if self.reason_code == "ZERO_WEIGHT" and self.status != "not_applicable":
            raise ValueError("ZERO_WEIGHT requires not_applicable")


def validate_index(result: MetricResult) -> None:
    """Numeric metric results used as indices must be within 0..100."""
    if not isinstance(result, MetricResult):
        raise ValueError("An index must be a MetricResult")
    if result.value is not None and result.value > 100:
        raise ValueError("An index must be within 0..100")
