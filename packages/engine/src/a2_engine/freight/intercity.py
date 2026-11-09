"""Complete freight delivery time and score from accepted chain components."""

from dataclasses import dataclass
from math import fsum

from a2_engine.shared.config import FreightConfig
from a2_engine.shared.results import MetricResult, finite_number
from a2_engine.shared.scoring import category_index


@dataclass(frozen=True)
class CargoDeliveryResult:
    cargo_profile_id: str
    urgency: str
    movement_seconds: MetricResult
    total_seconds: MetricResult
    index: MetricResult


def intercity_cargo_index(
    *,
    cargo_profile_id: str,
    urgency: str,
    initial_wait_seconds: float | None,
    movement_seconds: float | None,
    handling_seconds: float | None,
    transfer_wait_seconds: float | None,
    compatibility_confirmed: bool,
    config: FreightConfig,
) -> CargoDeliveryResult:
    """Sum nonoverlapping components once; urgency is independent of food/temperature."""
    if not isinstance(cargo_profile_id, str) or not cargo_profile_id.strip():
        raise ValueError("cargo_profile_id must be a nonempty string")
    if not isinstance(urgency, str) or urgency not in config.delivery_half_life_seconds:
        raise ValueError("Urgency must belong to the freight configuration")
    if not isinstance(compatibility_confirmed, bool):
        raise ValueError("compatibility_confirmed must be a boolean")
    components = {
        "initial_wait_seconds": initial_wait_seconds,
        "movement_seconds": movement_seconds,
        "handling_seconds": handling_seconds,
        "transfer_wait_seconds": transfer_wait_seconds,
    }
    checked = {
        key: None if value is None else finite_number(value, key)
        for key, value in components.items()
    }
    move = (
        MetricResult(checked["movement_seconds"], "available")
        if movement_seconds is not None
        else MetricResult(None, "unknown", "DATA_UNAVAILABLE")
    )
    if any(value is None for value in checked.values()):
        total = MetricResult(None, "unknown", "DATA_UNAVAILABLE")
    else:
        try:
            total = MetricResult(
                fsum(value for value in checked.values() if value is not None), "available"
            )
        except OverflowError as exc:
            raise ValueError("TOTAL_TIME_OVERFLOW") from exc
    index = category_index(total.value, config.delivery_half_life_seconds[urgency])
    if not compatibility_confirmed:
        index = MetricResult(None, "unknown", "COMPATIBILITY_UNCONFIRMED")
    return CargoDeliveryResult(cargo_profile_id, urgency, move, total, index)
