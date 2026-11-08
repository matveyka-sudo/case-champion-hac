"""Validate normalized urban intervals; never find or repair routes."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from math import fsum, isclose

from a2_engine.shared.config import UrbanJourneyRules
from a2_engine.shared.results import MetricResult
from a2_engine.urban.models import UrbanCandidate, UrbanLeg


@dataclass(frozen=True)
class UrbanJourneyTiming:
    candidate: UrbanCandidate
    total_seconds: float
    walking_seconds: float
    riding_seconds: float
    initial_wait_seconds: float
    transfer_wait_seconds: float
    total_wait_seconds: float
    waiting_share: MetricResult
    admissible: bool
    reason_code: str | None


@dataclass(frozen=True)
class UrbanLegTotals:
    total_seconds: float
    walking_seconds: float
    riding_seconds: float
    initial_wait_seconds: float
    transfer_wait_seconds: float
    total_wait_seconds: float
    waiting_share: MetricResult
    admissible: bool
    reason_code: str | None


def summarize_urban_legs(
    legs: Sequence[UrbanLeg],
    *,
    passenger_mode: str,
    departure_at: datetime,
    arrival_at: datetime,
    origin_node_id: str,
    destination_node_id: str,
    rules: UrbanJourneyRules,
) -> UrbanLegTotals:
    """Strict chain checks shared by forward candidates and separate return journeys."""
    if not legs:
        raise ValueError("EXPLICIT_LEG_SEQUENCE_REQUIRED")
    if legs[0].start_at != departure_at or legs[-1].end_at != arrival_at:
        raise ValueError("JOURNEY_TIME_BOUNDARY_MISMATCH")
    if legs[0].from_node_id != origin_node_id or legs[-1].to_node_id != destination_node_id:
        raise ValueError("JOURNEY_NODE_BOUNDARY_MISMATCH")
    allowed_modes = {
        "walk": {"walk"},
        "public_transport": {"walk", "public_transport", "rail", "water"},
        "private_car": {"walk", "private_car"},
    }[passenger_mode]
    rides = [position for position, leg in enumerate(legs) if leg.kind == "ride"]
    durations = []
    for position, leg in enumerate(legs):
        elapsed = (leg.end_at - leg.start_at).total_seconds()  # UTC normalized at the boundary.
        if elapsed < 0 or not isclose(elapsed, leg.duration_seconds, rel_tol=1e-12, abs_tol=1e-9):
            raise ValueError("LEG_DURATION_MISMATCH")
        durations.append(elapsed)
        if position and (
            legs[position - 1].end_at != leg.start_at
            or legs[position - 1].to_node_id != leg.from_node_id
        ):
            raise ValueError("DISCONNECTED_LEG_SEQUENCE")
        if leg.mode not in allowed_modes or (leg.kind == "walk") != (leg.mode == "walk"):
            raise ValueError("PASSENGER_MODE_MISMATCH")
        if passenger_mode == "walk" and leg.kind != "walk":
            raise ValueError("WALK_MODE_REQUIRES_WALK_LEGS")
        if leg.kind in ("wait_initial", "wait_transfer"):
            if leg.from_node_id != leg.to_node_id:
                raise ValueError("WAIT_CANNOT_CHANGE_NODE")
            if not rides or (leg.kind == "wait_initial" and position >= rides[0]):
                raise ValueError("INVALID_INITIAL_WAIT_POSITION")
            if leg.kind == "wait_transfer" and not rides[0] < position < rides[-1]:
                raise ValueError("INVALID_TRANSFER_WAIT_POSITION")
    walking = fsum(value for leg, value in zip(legs, durations, strict=True) if leg.kind == "walk")
    riding = fsum(value for leg, value in zip(legs, durations, strict=True) if leg.kind == "ride")
    initial = fsum(
        value for leg, value in zip(legs, durations, strict=True) if leg.kind == "wait_initial"
    )
    transfers = fsum(
        value for leg, value in zip(legs, durations, strict=True) if leg.kind == "wait_transfer"
    )
    total = fsum(durations)
    waiting = fsum(
        value
        for leg, value in zip(legs, durations, strict=True)
        if leg.kind in ("wait_initial", "wait_transfer")
    )
    share = (
        MetricResult(waiting / total, "available")
        if total > 0
        else MetricResult(None, "not_applicable", "ZERO_DURATION")
    )
    admissible = waiting <= rules.max_total_wait_seconds
    return UrbanLegTotals(
        total,
        walking,
        riding,
        initial,
        transfers,
        waiting,
        share,
        admissible,
        None if admissible else "MAX_TOTAL_WAIT_EXCEEDED",
    )


def validate_urban_journey(
    candidate: UrbanCandidate, *, rules: UrbanJourneyRules
) -> UrbanJourneyTiming:
    if candidate.context.versions.methodology_version != rules.methodology_version:
        raise ValueError("JOURNEY_METHODOLOGY_MISMATCH")
    if candidate.departure_at != candidate.context.requested_departure_at:
        raise ValueError("JOURNEY_TIME_BOUNDARY_MISMATCH")
    totals = summarize_urban_legs(
        candidate.legs,
        passenger_mode=candidate.passenger_mode,
        departure_at=candidate.departure_at,
        arrival_at=candidate.arrival_at,
        origin_node_id=candidate.origin_node_id,
        destination_node_id=candidate.destination_node_id,
        rules=rules,
    )
    return UrbanJourneyTiming(
        candidate,
        totals.total_seconds,
        totals.walking_seconds,
        totals.riding_seconds,
        totals.initial_wait_seconds,
        totals.transfer_wait_seconds,
        totals.total_wait_seconds,
        totals.waiting_share,
        totals.admissible,
        totals.reason_code,
    )
