"""Indices from accepted freight times and explicitly confirmed compatibility."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import fsum
from typing import Literal

from a2_engine.shared.results import MetricResult, validate_index
from a2_engine.shared.scoring import category_index


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
