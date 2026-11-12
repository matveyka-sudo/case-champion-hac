"""Physical requirements and evidence bindings, independent of scoring urgency."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal

from a2_engine.shared.normalized import checked_id, content_hash, ids, utc
from a2_engine.shared.results import finite_number


@dataclass(frozen=True)
class CargoTemperature:
    controlled: bool
    min_c: float | None
    max_c: float | None
    condition_id: str

    def __post_init__(self) -> None:
        checked_id(self.condition_id)
        if type(self.controlled) is not bool:
            raise ValueError("EXPLICIT_TEMPERATURE_CONTROL_REQUIRED")
        for key in ("min_c", "max_c"):
            value = getattr(self, key)
            if value is not None:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise ValueError("FINITE_CELSIUS_REQUIRED")
                try:
                    value = float(value)
                except OverflowError as exc:
                    raise ValueError("FINITE_CELSIUS_REQUIRED") from exc
                if not isfinite(value):
                    raise ValueError("FINITE_CELSIUS_REQUIRED")
                object.__setattr__(self, key, value)
        if self.controlled and (self.min_c is None or self.max_c is None):
            raise ValueError("CONTROLLED_TEMPERATURE_RANGE_REQUIRED")
        if self.min_c is not None and self.max_c is not None and self.min_c > self.max_c:
            raise ValueError("REVERSED_TEMPERATURE_RANGE")


@dataclass(frozen=True)
class CargoVehicle:
    vehicle_class: str
    mass_kg: float
    height_m: float
    width_m: float
    length_m: float
    access_class: str
    restrictions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        checked_id(self.vehicle_class)
        checked_id(self.access_class)
        object.__setattr__(self, "restrictions", ids(self.restrictions, required=False))
        for key in ("mass_kg", "height_m", "width_m", "length_m"):
            object.__setattr__(self, key, finite_number(getattr(self, key), key, positive=True))


@dataclass(frozen=True)
class CargoProfile:
    profile_id: str
    version: str
    goods: Literal["food", "nonfood", "raw"]
    urgency: str
    temperature: CargoTemperature
    handling_requirements: tuple[str, ...]
    access_requirements: tuple[str, ...]
    vehicle: CargoVehicle
    perishable: bool = False

    def __post_init__(self) -> None:
        for value in (self.profile_id, self.version, self.urgency):
            checked_id(value)
        if (
            self.goods not in ("food", "nonfood", "raw")
            or type(self.perishable) is not bool
            or not isinstance(self.vehicle, CargoVehicle)
            or not isinstance(self.temperature, CargoTemperature)
        ):
            raise ValueError("INVALID_CARGO_PROFILE")
        for key in ("handling_requirements", "access_requirements"):
            object.__setattr__(self, key, ids(getattr(self, key), required=False))

    @property
    def requirements_hash(self) -> str:
        # Urgency affects D, never silently changes physical compatibility.
        return content_hash(
            (
                self.profile_id,
                self.version,
                self.goods,
                self.temperature,
                self.handling_requirements,
                self.access_requirements,
                self.vehicle,
                *(("perishable",) if self.perishable else ()),
            )
        )


@dataclass(frozen=True)
class CargoContext:
    snapshot_id: str
    graph_version: str
    build_id: str
    calendar_version: str
    rules_version: str
    routing_profile: str
    methodology_version: str

    def __post_init__(self) -> None:
        for key in (
            "snapshot_id",
            "graph_version",
            "build_id",
            "calendar_version",
            "rules_version",
            "routing_profile",
        ):
            checked_id(getattr(self, key))
        if self.methodology_version != "a2-methodology-v3":
            raise ValueError("UNSUPPORTED_CARGO_METHODOLOGY")


@dataclass(frozen=True)
class CargoCompatibility:
    receipt_id: str
    context: CargoContext
    profile_id: str
    profile_version: str
    requirements_hash: str
    from_id: str
    to_id: str
    service_id: str
    direction: Literal["inbound", "outbound", "delivery"]
    valid_from: datetime
    valid_to: datetime
    state: Literal["confirmed", "forbidden", "unknown"]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for key in (
            "receipt_id",
            "profile_id",
            "profile_version",
            "requirements_hash",
            "from_id",
            "to_id",
            "service_id",
        ):
            checked_id(getattr(self, key))
        if (
            not isinstance(self.context, CargoContext)
            or self.direction not in ("inbound", "outbound", "delivery")
            or self.state not in ("confirmed", "forbidden", "unknown")
        ):
            raise ValueError("INVALID_CARGO_COMPATIBILITY")
        for key in ("valid_from", "valid_to"):
            object.__setattr__(self, key, utc(getattr(self, key)))
        if self.valid_from >= self.valid_to:
            raise ValueError("INVALID_COMPATIBILITY_PERIOD")
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))


def compatibility_state(
    receipt: CargoCompatibility,
    *,
    context: CargoContext,
    profile: CargoProfile,
    from_id: str,
    to_id: str,
    service_id: str,
    direction: str,
    start_at: datetime,
    end_at: datetime,
) -> Literal["confirmed", "forbidden", "unknown"]:
    if (
        receipt.context != context
        or receipt.profile_id != profile.profile_id
        or receipt.profile_version != profile.version
        or receipt.requirements_hash != profile.requirements_hash
        or (receipt.from_id, receipt.to_id, receipt.service_id, receipt.direction)
        != (from_id, to_id, service_id, direction)
    ):
        raise ValueError("CARGO_COMPATIBILITY_BINDING_MISMATCH")
    return (
        receipt.state
        if receipt.valid_from <= start_at < receipt.valid_to and end_at <= receipt.valid_to
        else "unknown"
    )
