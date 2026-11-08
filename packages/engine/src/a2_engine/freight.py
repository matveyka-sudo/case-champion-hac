"""Indices from accepted freight times and explicitly confirmed compatibility."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import fsum
from typing import Literal

from a2_engine.config import FreightConfig
from a2_engine.results import MetricResult, finite_number, validate_index
from a2_engine.urban import category_index


@dataclass(frozen=True)
class CargoScope:
    node_type: str
    direction: Literal["inbound", "outbound"]
    cargo_profile_id: str

    def __post_init__(self) -> None:
        for value in (self.node_type, self.cargo_profile_id):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Node type and cargo profile ID must be nonempty strings")
        if self.direction not in ("inbound", "outbound"):
            raise ValueError("Cargo direction must be inbound or outbound")


@dataclass(frozen=True)
class CargoSiteResult:
    site_id: str
    scope: CargoScope
    index: MetricResult

    def __post_init__(self) -> None:
        if not isinstance(self.site_id, str) or not self.site_id.strip():
            raise ValueError("site_id must identify a physical site")
        if not isinstance(self.scope, CargoScope):
            raise ValueError("scope must be a CargoScope")
        validate_index(self.index)


def urban_cargo_site_index(
    site_id: str,
    travel_seconds: float | None,
    h_seconds: float | None,
    *,
    scope: CargoScope,
    compatibility_confirmed: bool,
    confirmed_no_route: bool = False,
) -> CargoSiteResult:
    """H is the published urban H; no population or passenger priority is used."""
    if not isinstance(compatibility_confirmed, bool):
        raise ValueError("compatibility_confirmed must be a boolean")
    index = category_index(travel_seconds, h_seconds, confirmed_no_route=confirmed_no_route)
    if not compatibility_confirmed:
        index = MetricResult(None, "unknown", "COMPATIBILITY_UNCONFIRMED")
    return CargoSiteResult(site_id, scope, index)


def urban_cargo_territory_index(
    sites: Sequence[CargoSiteResult], *, scope: CargoScope
) -> MetricResult:
    """Equal physical-site mean for exactly one node type, direction and profile."""
    if not isinstance(scope, CargoScope):
        raise ValueError("scope must be a CargoScope")
    if not isinstance(sites, Sequence):
        raise ValueError("sites must be a sequence of physical-site results")
    seen: set[str] = set()
    for site in sites:
        if not isinstance(site, CargoSiteResult) or site.scope != scope:
            raise ValueError("Every site must match the requested cargo scope")
        if site.site_id in seen:
            raise ValueError(f"Duplicate physical site: {site.site_id}")
        seen.add(site.site_id)
    if not sites:
        return MetricResult(None, "not_applicable", "ZERO_WEIGHT")
    if any(site.index.value is None for site in sites):
        return MetricResult(None, "unknown", "SITE_INDEX_UNAVAILABLE")
    return MetricResult(
        fsum(site.index.value for site in sites if site.index.value is not None) / len(sites),
        "available",
    )


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
