from dataclasses import replace
from typing import cast

import pytest
from a2_engine import (
    CargoScope,
    CargoSiteResult,
    FreightConfig,
    MetricResult,
    intercity_cargo_index,
    select_h_seconds,
    urban_cargo_site_index,
    urban_cargo_territory_index,
)

SCOPE = CargoScope("port", "outbound", "synthetic-dry-cargo")


def test_urban_cargo_reuses_h_and_equal_physical_sites(freight_config: FreightConfig) -> None:
    h = select_h_seconds(100, bands=freight_config.area_h_bands).value
    first = urban_cargo_site_index("site-a", 0, h, scope=SCOPE, compatibility_confirmed=True)
    second = urban_cargo_site_index(
        "site-b", None, h, scope=SCOPE, compatibility_confirmed=True, confirmed_no_route=True
    )
    assert first.index.value == 100
    assert second.index == MetricResult(0, "available", "NO_FEASIBLE_ROUTE")
    assert urban_cargo_territory_index([first, second], scope=SCOPE).value == 50
    assert (
        urban_cargo_site_index(
            "site-c", 1200, h, scope=SCOPE, compatibility_confirmed=True
        ).index.value
        == 50
    )


def test_unknown_site_is_not_dropped() -> None:
    sites = [
        CargoSiteResult("a", SCOPE, MetricResult(100, "available")),
        urban_cargo_site_index("b", None, 1200, scope=SCOPE, compatibility_confirmed=True),
    ]
    assert urban_cargo_territory_index(sites, scope=SCOPE) == MetricResult(
        None, "unknown", "SITE_INDEX_UNAVAILABLE"
    )
    assert urban_cargo_territory_index([], scope=SCOPE) == MetricResult(
        None, "not_applicable", "ZERO_WEIGHT"
    )


def test_physical_site_duplicates_are_invalid() -> None:
    site = CargoSiteResult("one-physical-site", SCOPE, MetricResult(100, "available"))
    with pytest.raises(ValueError, match="Duplicate physical site"):
        urban_cargo_territory_index([site, site], scope=SCOPE)


@pytest.mark.parametrize(
    "scope",
    [
        replace(SCOPE, node_type="rail"),
        replace(SCOPE, direction="inbound"),
        replace(SCOPE, cargo_profile_id="other-profile"),
    ],
)
def test_mixed_node_direction_or_profile_is_rejected(scope: CargoScope) -> None:
    site = CargoSiteResult("a", scope, MetricResult(100, "available"))
    with pytest.raises(ValueError, match="scope"):
        urban_cargo_territory_index([site], scope=SCOPE)


def test_compatibility_must_be_explicit() -> None:
    result = urban_cargo_site_index("a", 600, 1200, scope=SCOPE, compatibility_confirmed=False)
    assert result.index == MetricResult(None, "unknown", "COMPATIBILITY_UNCONFIRMED")


@pytest.mark.parametrize(
    "urgency, seconds", [("urgent", 86400), ("ordinary", 259200), ("nonurgent", 2678400)]
)
def test_acceptance_delivery_half_lives(
    freight_config: FreightConfig, urgency: str, seconds: float
) -> None:
    for factor, expected in [(1, 50), (2, 25)]:
        result = intercity_cargo_index(
            cargo_profile_id="synthetic-food-chilled",
            urgency=urgency,
            initial_wait_seconds=0,
            movement_seconds=seconds * factor,
            handling_seconds=0,
            transfer_wait_seconds=0,
            compatibility_confirmed=True,
            config=freight_config,
        )
        assert result.total_seconds.value == seconds * factor
        assert result.index.value == expected


def test_delivery_components_are_counted_once(freight_config: FreightConfig) -> None:
    result = intercity_cargo_index(
        cargo_profile_id="synthetic-nonfood",
        urgency="urgent",
        initial_wait_seconds=3600,
        movement_seconds=80000,
        handling_seconds=1800,
        transfer_wait_seconds=1000,
        compatibility_confirmed=True,
        config=freight_config,
    )
    assert result.total_seconds.value == 86400
    assert result.movement_seconds.value == 80000
    assert result.index.value == 50


@pytest.mark.parametrize(
    "missing",
    ["initial_wait_seconds", "movement_seconds", "handling_seconds", "transfer_wait_seconds"],
)
def test_any_missing_delivery_component_blocks_full_score(
    freight_config: FreightConfig, missing: str
) -> None:
    parts: dict[str, float | None] = dict(
        initial_wait_seconds=0, movement_seconds=86400, handling_seconds=0, transfer_wait_seconds=0
    )
    parts[missing] = None
    result = intercity_cargo_index(
        cargo_profile_id="test",
        urgency="urgent",
        compatibility_confirmed=True,
        config=freight_config,
        **parts,
    )
    assert result.total_seconds == MetricResult(None, "unknown", "DATA_UNAVAILABLE")
    assert result.index.value is None
    if missing != "movement_seconds":
        assert result.movement_seconds.value == 86400


def test_unconfirmed_delivery_preserves_raw_time_but_not_profile_score(
    freight_config: FreightConfig,
) -> None:
    result = intercity_cargo_index(
        cargo_profile_id="test",
        urgency="urgent",
        initial_wait_seconds=0,
        movement_seconds=86400,
        handling_seconds=0,
        transfer_wait_seconds=0,
        compatibility_confirmed=False,
        config=freight_config,
    )
    assert result.total_seconds.value == 86400
    assert result.index == MetricResult(None, "unknown", "COMPATIBILITY_UNCONFIRMED")


@pytest.mark.parametrize("bad", [-1, True, "1", float("nan"), float("inf")])
def test_invalid_freight_numbers_are_not_masked_by_missing_data(
    freight_config: FreightConfig, bad: object
) -> None:
    with pytest.raises(ValueError):
        urban_cargo_site_index(
            "a", cast(float, bad), 1200, scope=SCOPE, compatibility_confirmed=False
        )
    with pytest.raises(ValueError):
        intercity_cargo_index(
            cargo_profile_id="test",
            urgency="urgent",
            initial_wait_seconds=None,
            movement_seconds=0,
            handling_seconds=cast(float, bad),
            transfer_wait_seconds=0,
            compatibility_confirmed=False,
            config=freight_config,
        )


def test_invalid_urgency_and_overflow(freight_config: FreightConfig) -> None:
    for urgency, reason in [("food", "Urgency"), ("urgent", "TOTAL_TIME_OVERFLOW")]:
        with pytest.raises(ValueError, match=reason):
            intercity_cargo_index(
                cargo_profile_id="test",
                urgency=urgency,
                initial_wait_seconds=1e308,
                movement_seconds=1e308,
                handling_seconds=0,
                transfer_wait_seconds=0,
                compatibility_confirmed=True,
                config=freight_config,
            )
