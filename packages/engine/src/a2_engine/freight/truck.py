"""Accepted freight routing boundary, never a passenger auto result cast to truck."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from a2_engine.freight.models import CargoCompatibility, CargoContext, CargoVehicle
from a2_engine.shared.normalized import checked_id, content_hash, duration, ids, utc
from a2_engine.shared.results import finite_number


@dataclass(frozen=True)
class TruckRoute:
    route_id: str
    context: CargoContext
    requirements_hash: str
    vehicle: CargoVehicle
    from_id: str
    to_id: str
    service_id: str
    direction: Literal["inbound", "outbound"]
    requested_at: datetime
    network_seconds: float | None
    origin_access_seconds: float | None
    destination_access_seconds: float | None
    status: Literal["reachable", "confirmed_unreachable", "unknown"]
    compatibility: CargoCompatibility
    evidence_refs: tuple[str, ...]
    costing: Literal["truck"] = "truck"

    def __post_init__(self) -> None:
        for key in ("route_id", "requirements_hash", "from_id", "to_id", "service_id"):
            checked_id(getattr(self, key))
        if (
            not isinstance(self.context, CargoContext)
            or not isinstance(self.vehicle, CargoVehicle)
            or not isinstance(self.compatibility, CargoCompatibility)
        ):
            raise ValueError("TRUCK_CONTEXT_AND_COMPATIBILITY_REQUIRED")
        if (
            self.costing != "truck"
            or self.vehicle.vehicle_class != "truck"
            or self.direction not in ("inbound", "outbound")
            or self.status not in ("reachable", "confirmed_unreachable", "unknown")
        ):
            raise ValueError("CONFIRMED_TRUCK_SEMANTICS_REQUIRED")
        object.__setattr__(self, "requested_at", utc(self.requested_at))
        for key in ("origin_access_seconds", "destination_access_seconds"):
            value = getattr(self, key)
            if value is not None:
                object.__setattr__(self, key, finite_number(value, key))
                duration(value)
        if self.network_seconds is not None:
            object.__setattr__(
                self, "network_seconds", finite_number(self.network_seconds, "network seconds")
            )
            duration(self.network_seconds)
        if (self.status == "reachable") != (self.network_seconds is not None):
            raise ValueError("TRUCK_STATUS_TIME_MISMATCH")
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))
        if self.seconds is not None:
            duration(self.seconds)

    @property
    def seconds(self) -> float | None:
        if (
            self.network_seconds is None
            or self.origin_access_seconds is None
            or self.destination_access_seconds is None
        ):
            return None
        try:
            return sum(
                (
                    duration(value)
                    for value in (
                        self.network_seconds,
                        self.origin_access_seconds,
                        self.destination_access_seconds,
                    )
                ),
                timedelta(),
            ).total_seconds()
        except OverflowError as exc:
            raise ValueError("TRUCK_TIME_OVERFLOW") from exc

    @property
    def fingerprint(self) -> str:
        return content_hash(self)


@dataclass(frozen=True)
class TruckSearchReceipt:
    route_id: str
    route_fingerprint: str
    search_scope_id: str
    complete: bool
    minimum_confirmed: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.route_id, self.route_fingerprint, self.search_scope_id):
            checked_id(value)
        if type(self.complete) is not bool or type(self.minimum_confirmed) is not bool:
            raise ValueError("EXPLICIT_TRUCK_SEARCH_STATE_REQUIRED")
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))
