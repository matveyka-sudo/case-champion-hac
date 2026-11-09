"""Load and validate published methodology configurations, independently of cwd."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from types import MappingProxyType

from a2_engine.shared.results import finite_number


@dataclass(frozen=True)
class AreaHBand:
    max_area_km2: float | None
    h_seconds: float

    def __post_init__(self) -> None:
        if self.max_area_km2 is not None:
            object.__setattr__(
                self,
                "max_area_km2",
                finite_number(self.max_area_km2, "max_area_km2", positive=True),
            )
        object.__setattr__(
            self, "h_seconds", finite_number(self.h_seconds, "h_seconds", positive=True)
        )


def validate_bands(bands: Sequence[AreaHBand]) -> None:
    if not bands:
        raise ValueError("area_h_bands must not be empty")
    previous = 0.0
    for position, band in enumerate(bands):
        if not isinstance(band, AreaHBand):
            raise ValueError("area_h_bands must contain AreaHBand values")
        if band.max_area_km2 is None:
            if position != len(bands) - 1:
                raise ValueError("Only the final area band may be unbounded")
        elif band.max_area_km2 <= previous:
            raise ValueError("Area band limits must be strictly increasing")
        else:
            previous = band.max_area_km2
    if bands[-1].max_area_km2 is not None:
        raise ValueError("The final area band must be unbounded")


def _identifiers(values: object, name: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple, frozenset)) or not values:
        raise ValueError(f"{name} must be a nonempty collection of identifiers")
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{name} must contain nonempty strings")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicate identifiers")
    return tuple(values)


@dataclass(frozen=True)
class UrbanConfig:
    methodology_version: str
    area_h_bands: tuple[AreaHBand, ...]
    priority_weights: Mapping[str, float]
    categories: frozenset[str]
    modes: frozenset[str]
    low_accessibility_below: float

    def __post_init__(self) -> None:
        if not isinstance(self.methodology_version, str) or not self.methodology_version.strip():
            raise ValueError("methodology_version must be a nonempty string")
        bands = tuple(self.area_h_bands)
        validate_bands(bands)
        object.__setattr__(self, "area_h_bands", bands)
        if not isinstance(self.priority_weights, Mapping) or not self.priority_weights:
            raise ValueError("priority_weights must be a nonempty mapping")
        _identifiers(tuple(self.priority_weights), "priority_weights")
        weights = {
            key: finite_number(value, f"priority_weights.{key}", positive=True)
            for key, value in self.priority_weights.items()
        }
        object.__setattr__(self, "priority_weights", MappingProxyType(weights))
        object.__setattr__(
            self, "categories", frozenset(_identifiers(self.categories, "categories"))
        )
        object.__setattr__(self, "modes", frozenset(_identifiers(self.modes, "urban_modes")))
        threshold = finite_number(self.low_accessibility_below, "low_accessibility_below")
        if threshold > 100:
            raise ValueError("low_accessibility_below must be within 0..100")
        object.__setattr__(self, "low_accessibility_below", threshold)


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object with string keys")
    return value


def urban_config_from_mapping(product: Mapping[str, object]) -> UrbanConfig:
    """Copy the urban subset of product.json; reject unapproved/mismatched versions."""
    try:
        methods = _mapping(product["methodologies"], "methodologies")
        urban = _mapping(methods["urban_passenger"], "urban_passenger")
        version = product["methodology_version"]
        if not isinstance(version, str) or urban["version"] != version:
            raise ValueError("Global and urban methodology versions must agree")
        if urban["status"] != "approved":
            raise ValueError("The urban methodology must be approved")
        parameters = _mapping(urban["parameters"], "urban parameters")
        raw_bands = parameters["area_h_bands"]
        if not isinstance(raw_bands, list):
            raise ValueError("area_h_bands must be a list")
        bands = []
        for item in raw_bands:
            band = _mapping(item, "area band")
            area = band["max_area_km2"]
            minutes = finite_number(band["h_minutes"], "h_minutes", positive=True)
            bands.append(
                AreaHBand(
                    None if area is None else finite_number(area, "max_area_km2", positive=True),
                    minutes * 60.0,
                )
            )
        levels = _identifiers(product["priority_levels"], "priority_levels")
        raw_weights = _mapping(product["priority_weights"], "priority_weights")
        if set(levels) != set(raw_weights):
            raise ValueError("priority_weights must match priority_levels exactly")
        return UrbanConfig(
            methodology_version=version,
            area_h_bands=tuple(bands),
            priority_weights={
                key: finite_number(value, f"priority_weights.{key}", positive=True)
                for key, value in raw_weights.items()
            },
            categories=frozenset(_identifiers(product["categories"], "categories")),
            modes=frozenset(_identifiers(product["urban_modes"], "urban_modes")),
            low_accessibility_below=finite_number(
                parameters["low_accessibility_below"], "low_accessibility_below"
            ),
        )
    except KeyError as exc:
        raise ValueError(f"Missing configuration field: {exc.args[0]}") from exc


def load_urban_config(path: str | Path | None = None) -> UrbanConfig:
    """Read product.json. Installed distributions must receive an explicit path."""
    return urban_config_from_mapping(_load_product(path))


def _load_product(path: str | Path | None) -> Mapping[str, object]:
    # shared/config.py is one level below the former flat package module.
    config_path = (
        Path(path)
        if path is not None
        else Path(__file__).resolve().parents[5] / "config/product.json"
    )
    product: object = json.loads(config_path.read_text(encoding="utf-8"))
    return _mapping(product, "product")


def _method_parameters(
    product: Mapping[str, object], kind: str
) -> tuple[str, Mapping[str, object]]:
    try:
        method = _mapping(_mapping(product["methodologies"], "methodologies")[kind], kind)
        version = product["methodology_version"]
        if not isinstance(version, str) or not version.strip() or method["version"] != version:
            raise ValueError("Global and method versions must agree")
        if method["status"] != "approved":
            raise ValueError(f"{kind} must be approved")
        return version, _mapping(method["parameters"], f"{kind} parameters")
    except KeyError as exc:
        raise ValueError(f"Missing configuration field: {exc.args[0]}") from exc


@dataclass(frozen=True)
class PassengerConfig:
    methodology_version: str
    waiting_half_life_seconds: float
    reference_profile: str

    def __post_init__(self) -> None:
        _identifiers((self.methodology_version,), "methodology_version")
        _identifiers((self.reference_profile,), "reference_profile")
        object.__setattr__(
            self,
            "waiting_half_life_seconds",
            finite_number(
                self.waiting_half_life_seconds, "waiting_half_life_seconds", positive=True
            ),
        )


def passenger_config_from_mapping(product: Mapping[str, object]) -> PassengerConfig:
    version, parameters = _method_parameters(product, "intercity_passenger")
    try:
        if parameters["reference"] != "A":
            raise ValueError("Only reference A is implemented")
        profile = parameters["reference_profile"]
        if not isinstance(profile, str):
            raise ValueError("reference_profile must be a string")
        hours = finite_number(
            parameters["initial_wait_half_life_hours"], "waiting hours", positive=True
        )
        return PassengerConfig(version, hours * 3600.0, profile)
    except KeyError as exc:
        raise ValueError(f"Missing configuration field: {exc.args[0]}") from exc


def load_passenger_config(path: str | Path | None = None) -> PassengerConfig:
    return passenger_config_from_mapping(_load_product(path))


@dataclass(frozen=True)
class FreightConfig:
    methodology_version: str
    area_h_bands: tuple[AreaHBand, ...]
    delivery_half_life_seconds: Mapping[str, float]
    pipeline_products: frozenset[str]

    def __post_init__(self) -> None:
        _identifiers((self.methodology_version,), "methodology_version")
        bands = tuple(self.area_h_bands)
        validate_bands(bands)
        object.__setattr__(self, "area_h_bands", bands)
        if not isinstance(self.delivery_half_life_seconds, Mapping):
            raise ValueError("delivery_half_life_seconds must be a mapping")
        _identifiers(tuple(self.delivery_half_life_seconds), "urgencies")
        half_lives = {
            key: finite_number(value, f"delivery half-life.{key}", positive=True)
            for key, value in self.delivery_half_life_seconds.items()
        }
        object.__setattr__(self, "delivery_half_life_seconds", MappingProxyType(half_lives))
        object.__setattr__(
            self,
            "pipeline_products",
            frozenset(_identifiers(self.pipeline_products, "pipeline_products")),
        )


def freight_config_from_mapping(product: Mapping[str, object]) -> FreightConfig:
    version, delivery = _method_parameters(product, "intercity_cargo")
    _, urban = _method_parameters(product, "urban_cargo")
    _, passenger_urban = _method_parameters(product, "urban_passenger")
    try:
        if urban["h_source"] != "urban_passenger.area_h_bands":
            raise ValueError("Unsupported urban cargo H source")
        if urban["aggregation"] != "equal_physical_sites_by_node_type":
            raise ValueError("Unsupported urban cargo aggregation")
        if delivery["pipeline_in_total"] is not False:
            raise ValueError("Pipelines must stay outside the cargo delivery index")
        raw_bands = passenger_urban["area_h_bands"]
        if not isinstance(raw_bands, list):
            raise ValueError("area_h_bands must be a list")
        bands = []
        for item in raw_bands:
            band = _mapping(item, "area band")
            area = band["max_area_km2"]
            bands.append(
                AreaHBand(
                    None if area is None else finite_number(area, "max_area_km2", positive=True),
                    finite_number(band["h_minutes"], "h_minutes", positive=True) * 60.0,
                )
            )
        hours = _mapping(delivery["delivery_half_life_hours"], "delivery_half_life_hours")
        return FreightConfig(
            version,
            tuple(bands),
            {
                key: finite_number(value, f"delivery hours.{key}", positive=True) * 3600.0
                for key, value in hours.items()
            },
            frozenset(_identifiers(product["pipeline_products"], "pipeline_products")),
        )
    except KeyError as exc:
        raise ValueError(f"Missing configuration field: {exc.args[0]}") from exc


def load_freight_config(path: str | Path | None = None) -> FreightConfig:
    return freight_config_from_mapping(_load_product(path))


@dataclass(frozen=True)
class UrbanJourneyRules:
    methodology_version: str
    max_total_wait_seconds: float

    def __post_init__(self) -> None:
        _identifiers((self.methodology_version,), "methodology_version")
        object.__setattr__(
            self,
            "max_total_wait_seconds",
            finite_number(self.max_total_wait_seconds, "max_total_wait_seconds"),
        )


def urban_journey_rules_from_mapping(product: Mapping[str, object]) -> UrbanJourneyRules:
    version, parameters = _method_parameters(product, "urban_passenger")
    try:
        hours = finite_number(parameters["max_total_wait_hours"], "max_total_wait_hours")
        return UrbanJourneyRules(version, hours * 3600.0)
    except KeyError as exc:
        raise ValueError(f"Missing configuration field: {exc.args[0]}") from exc


def load_urban_journey_rules(path: str | Path | None = None) -> UrbanJourneyRules:
    return urban_journey_rules_from_mapping(_load_product(path))


@dataclass(frozen=True)
class UrbanTauRules:
    methodology_version: str
    tau_days: int
    tau_times: tuple[time, ...]
    max_total_wait_seconds: float
    # IMPLEMENTATION.md, "Порог τ": eight preceding completed calendar weeks.
    max_lookback_weeks: int = 8

    def __post_init__(self) -> None:
        _identifiers((self.methodology_version,), "methodology_version")
        if (
            isinstance(self.tau_days, bool)
            or not isinstance(self.tau_days, int)
            or not 1 <= self.tau_days <= 7
        ):
            raise ValueError("tau_days must select 1..7 days of an ISO week")
        if (
            isinstance(self.max_lookback_weeks, bool)
            or not isinstance(self.max_lookback_weeks, int)
            or self.max_lookback_weeks <= 0
        ):
            raise ValueError("max_lookback_weeks must be a positive integer")
        clocks = tuple(self.tau_times)
        if not clocks or any(
            not isinstance(clock, time)
            or clock.tzinfo is not None
            or clock.second
            or clock.microsecond
            for clock in clocks
        ):
            raise ValueError("tau_times must be nonempty naive local minute times")
        if len(set(clocks)) != len(clocks):
            raise ValueError("Duplicate tau_times")
        object.__setattr__(self, "tau_times", tuple(sorted(clocks)))
        object.__setattr__(
            self,
            "max_total_wait_seconds",
            finite_number(self.max_total_wait_seconds, "max_total_wait_seconds"),
        )


def urban_tau_rules_from_mapping(product: Mapping[str, object]) -> UrbanTauRules:
    version, parameters = _method_parameters(product, "urban_passenger")
    try:
        days = parameters["tau_days"]
        if not isinstance(days, int) or isinstance(days, bool):
            raise ValueError("tau_days must be an integer")
        raw_times = parameters["tau_times"]
        if not isinstance(raw_times, list):
            raise ValueError("tau_times must be a list of HH:MM strings")
        times = []
        for raw in raw_times:
            if not isinstance(raw, str) or len(raw) != 5:
                raise ValueError("tau_times must use HH:MM")
            clock = time.fromisoformat(raw)
            if clock.isoformat(timespec="minutes") != raw:
                raise ValueError("tau_times must use HH:MM")
            times.append(clock)
        hours = finite_number(parameters["max_total_wait_hours"], "max_total_wait_hours")
        return UrbanTauRules(version, days, tuple(times), hours * 3600.0)
    except KeyError as exc:
        raise ValueError(f"Missing configuration field: {exc.args[0]}") from exc


def load_urban_tau_rules(path: str | Path | None = None) -> UrbanTauRules:
    return urban_tau_rules_from_mapping(_load_product(path))


@dataclass(frozen=True)
class IntercityPassengerRules:
    methodology_version: str
    departure_window_days: int

    def __post_init__(self) -> None:
        if self.methodology_version != "a2-methodology-v3":
            raise ValueError("UNSUPPORTED_INTERCITY_PASSENGER_METHODOLOGY")
        if type(self.departure_window_days) is not int or self.departure_window_days <= 0:
            raise ValueError("POSITIVE_DEPARTURE_WINDOW_DAYS_REQUIRED")


def intercity_passenger_rules_from_mapping(
    product: Mapping[str, object],
) -> IntercityPassengerRules:
    version, parameters = _method_parameters(product, "intercity_passenger")
    try:
        days = parameters["departure_window_days"]
        if type(days) is not int:
            raise ValueError("INTEGER_DEPARTURE_WINDOW_DAYS_REQUIRED")
        return IntercityPassengerRules(version, days)
    except KeyError as exc:
        raise ValueError("Missing departure_window_days") from exc


def load_intercity_passenger_rules(path: str | Path | None = None) -> IntercityPassengerRules:
    return intercity_passenger_rules_from_mapping(_load_product(path))


@dataclass(frozen=True)
class CargoDeliveryRules:
    methodology_version: str
    departure_window_days: int

    def __post_init__(self) -> None:
        if self.methodology_version != "a2-methodology-v3":
            raise ValueError("UNSUPPORTED_CARGO_METHODOLOGY")
        if type(self.departure_window_days) is not int or self.departure_window_days <= 0:
            raise ValueError("POSITIVE_CARGO_DEPARTURE_DAYS_REQUIRED")


def cargo_delivery_rules_from_mapping(product: Mapping[str, object]) -> CargoDeliveryRules:
    version, parameters = _method_parameters(product, "intercity_cargo")
    try:
        days = parameters["departure_window_days"]
        if type(days) is not int:
            raise ValueError("INTEGER_CARGO_DEPARTURE_DAYS_REQUIRED")
        return CargoDeliveryRules(version, days)
    except KeyError as exc:
        raise ValueError("Missing cargo departure_window_days") from exc


def load_cargo_delivery_rules(path: str | Path | None = None) -> CargoDeliveryRules:
    return cargo_delivery_rules_from_mapping(_load_product(path))
