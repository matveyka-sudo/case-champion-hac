"""Finite accepted cargo execution/operation graph, not passenger footpaths."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from a2_engine.freight.intercity import CargoDeliveryResult
from a2_engine.freight.models import CargoCompatibility, CargoContext, CargoProfile
from a2_engine.shared.normalized import checked_id, duration, ids, utc
from a2_engine.shared.results import finite_number


@dataclass(frozen=True)
class CargoDeliveryScope:
    context: CargoContext
    requirements_hash: str
    origin_territory_id: str
    destination_territory_id: str
    origin_node_ids: tuple[str, ...]
    destination_node_ids: tuple[str, ...]
    start_at: datetime
    end_at: datetime
    allowed_modes: tuple[str, ...]
    services_complete: bool
    calendars_complete: bool
    operations_complete: bool
    compatibility_complete: bool
    modes_complete: bool
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.context, CargoContext):
            raise ValueError("CARGO_CONTEXT_REQUIRED")
        for value in (
            self.requirements_hash,
            self.origin_territory_id,
            self.destination_territory_id,
        ):
            checked_id(value)
        for key in ("origin_node_ids", "destination_node_ids", "allowed_modes", "evidence_refs"):
            object.__setattr__(self, key, ids(getattr(self, key)))
        if self.origin_territory_id == self.destination_territory_id or set(
            self.origin_node_ids
        ) & set(self.destination_node_ids):
            raise ValueError("DISTINCT_CARGO_BOUNDARIES_REQUIRED")
        if set(self.allowed_modes) - {"road", "rail", "air", "water"}:
            raise ValueError("PIPELINE_NOT_DELIVERY_MODE")
        for key in ("start_at", "end_at"):
            object.__setattr__(self, key, utc(getattr(self, key)))
        if self.start_at >= self.end_at:
            raise ValueError("FINITE_CARGO_HORIZON_REQUIRED")
        for key in (
            "services_complete",
            "calendars_complete",
            "operations_complete",
            "compatibility_complete",
            "modes_complete",
        ):
            if type(getattr(self, key)) is not bool:
                raise ValueError("EXPLICIT_CARGO_COVERAGE_REQUIRED")


@dataclass(frozen=True)
class CargoState:
    state_id: str
    node_id: str
    stage: Literal["unprepared", "prepared", "in_transit", "completed"]
    execution_id: str | None
    conditions_id: str

    def __post_init__(self) -> None:
        for value in (self.state_id, self.node_id, self.conditions_id):
            checked_id(value)
        if self.stage not in ("unprepared", "prepared", "in_transit", "completed"):
            raise ValueError("INVALID_CARGO_STAGE")
        if self.execution_id is not None:
            checked_id(self.execution_id)
        if self.stage in ("prepared", "in_transit") and self.execution_id is None:
            raise ValueError("CARGO_EXECUTION_REQUIRED")


@dataclass(frozen=True)
class CargoWaitPolicy:
    kind: Literal["initial_wait", "transfer_wait"]
    compatibility: CargoCompatibility

    def __post_init__(self) -> None:
        if self.kind not in ("initial_wait", "transfer_wait") or not isinstance(
            self.compatibility, CargoCompatibility
        ):
            raise ValueError("EXPLICIT_CARGO_WAIT_SEMANTICS_REQUIRED")


@dataclass(frozen=True)
class CargoAction:
    action_id: str
    from_state_id: str
    to_state_id: str
    service_id: str
    kind: Literal["movement", "handling"]
    seconds: float | None
    scheduled_at: datetime | None
    mode: Literal["road", "rail", "air", "water"] | None
    necessity: Literal["required", "not_required", "unknown"]
    compatibility: CargoCompatibility
    wait_policy: CargoWaitPolicy | None
    active: bool | None
    calendar_evidence_refs: tuple[str, ...]
    requires_operations: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (self.action_id, self.from_state_id, self.to_state_id, self.service_id):
            checked_id(value)
        if self.kind not in ("movement", "handling") or self.necessity not in (
            "required",
            "not_required",
            "unknown",
        ):
            raise ValueError("INVALID_CARGO_ACTION")
        if self.active is not None and type(self.active) is not bool:
            raise ValueError("EXPLICIT_CARGO_CALENDAR_REQUIRED")
        if self.seconds is not None:
            object.__setattr__(self, "seconds", finite_number(self.seconds, "cargo action seconds"))
            duration(self.seconds)
        if self.scheduled_at is not None:
            object.__setattr__(self, "scheduled_at", utc(self.scheduled_at))
        if self.kind == "movement" and (
            self.scheduled_at is None
            or self.mode not in ("road", "rail", "air", "water")
            or self.necessity != "required"
        ):
            raise ValueError("SCHEDULED_FREIGHT_SERVICE_REQUIRED")
        if self.kind == "handling" and self.mode is not None:
            raise ValueError("HANDLING_DOES_NOT_ADD_MOVEMENT_MODE")
        if self.necessity == "not_required" and self.seconds != 0:
            raise ValueError("CONFIRMED_ABSENCE_REQUIRES_ZERO_OPERATION")
        if not isinstance(self.compatibility, CargoCompatibility) or (
            self.wait_policy is not None and not isinstance(self.wait_policy, CargoWaitPolicy)
        ):
            raise ValueError("CARGO_COMPATIBILITY_REQUIRED")
        object.__setattr__(
            self, "requires_operations", ids(self.requires_operations, required=False)
        )
        object.__setattr__(
            self, "calendar_evidence_refs", ids(self.calendar_evidence_refs, required=False)
        )
        object.__setattr__(self, "evidence_refs", ids(self.evidence_refs))


@dataclass(frozen=True)
class CargoReadiness:
    state_id: str
    ready_at: datetime
    compatibility: CargoCompatibility

    def __post_init__(self) -> None:
        checked_id(self.state_id)
        object.__setattr__(self, "ready_at", utc(self.ready_at))
        if not isinstance(self.compatibility, CargoCompatibility):
            raise ValueError("CARGO_READINESS_PROOF_REQUIRED")


@dataclass(frozen=True)
class CargoInterval:
    action_id: str
    kind: str
    from_state_id: str
    to_state_id: str
    start_at: datetime
    end_at: datetime
    evidence_refs: tuple[str, ...]

    @property
    def seconds(self) -> float:
        return (self.end_at - self.start_at).total_seconds()


@dataclass(frozen=True)
class CargoDeliveryPath:
    path_id: str
    origin_node_id: str
    destination_node_id: str
    completed_at: datetime | None
    modes: tuple[str, ...]
    action_ids: tuple[str, ...]
    intervals: tuple[CargoInterval, ...]
    evaluation: CargoDeliveryResult
    blocked_action_id: str | None
    reason_code: str | None

    @property
    def known_movement_seconds(self) -> float:
        return sum(i.seconds for i in self.intervals if i.kind == "movement")

    @property
    def category(self) -> str:
        return self.modes[0] if len(self.modes) == 1 else "mixed" if self.modes else "none"


@dataclass(frozen=True)
class CargoModeAnalysis:
    category: str
    best_known: CargoDeliveryPath | None
    optimum_proven: bool


@dataclass(frozen=True)
class IntercityCargoAnalysis:
    analysis_id: str
    scope: CargoDeliveryScope
    profile: CargoProfile
    started_at: datetime
    first_departures_end_at: datetime
    states: tuple[CargoState, ...]
    actions: tuple[CargoAction, ...]
    readiness: tuple[CargoReadiness, ...]
    candidates: tuple[CargoDeliveryPath, ...]
    incomplete_paths: tuple[CargoDeliveryPath, ...]
    by_mode: tuple[CargoModeAnalysis, ...]
    overall: CargoModeAnalysis
    connection_state: Literal["reachable", "confirmed_absent", "unknown"]
    data_complete: bool
    search_complete: bool
    expanded_states: int
    interrupted: bool
    full_ranking_eligible: bool
    excluded_actions: tuple[tuple[str, str], ...]
    reasons: tuple[str, ...]
    evidence_refs: tuple[str, ...]
