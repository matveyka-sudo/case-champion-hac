"""Computed 24h cargo delivery, including loading and final unloading."""

from datetime import UTC, datetime, timedelta

from a2_engine.freight.delivery.models import (
    CargoAction,
    CargoDeliveryScope,
    CargoReadiness,
    CargoState,
    CargoWaitPolicy,
    IntercityCargoAnalysis,
)
from a2_engine.freight.delivery.search import analyze_intercity_cargo
from a2_engine.freight.models import (
    CargoCompatibility,
    CargoContext,
    CargoProfile,
    CargoTemperature,
    CargoVehicle,
)
from a2_engine.shared.config import load_cargo_delivery_rules, load_freight_config

START = datetime(2026, 11, 9, tzinfo=UTC)
PROOF = ("synthetic-proof",)
CONTEXT = CargoContext(
    "snapshot", "cargo-graph", "cargo-build", "calendar", "rules", "cargo-v1", "a2-methodology-v3"
)
PROFILE = CargoProfile(
    "food-ambient",
    "v1",
    "food",
    "urgent",
    CargoTemperature(False, None, None, "ambient"),
    ("sealed",),
    ("freight",),
    CargoVehicle("truck", 12000, 3, 2.5, 8, "freight"),
)


def receipt(origin: str, target: str, service: str) -> CargoCompatibility:
    return CargoCompatibility(
        "proof:" + service,
        CONTEXT,
        PROFILE.profile_id,
        PROFILE.version,
        PROFILE.requirements_hash,
        origin,
        target,
        service,
        "delivery",
        START - timedelta(days=1),
        START + timedelta(days=100),
        "confirmed",
        PROOF,
    )


def demo_inputs() -> tuple[
    CargoDeliveryScope, tuple[CargoState, ...], tuple[CargoAction, ...], tuple[CargoReadiness, ...]
]:
    states = (
        CargoState("origin", "A", "unprepared", None, "ambient"),
        CargoState("loaded-road", "A", "prepared", "road-service", "sealed"),
        CargoState("road-arrival", "X", "in_transit", "road-service", "sealed"),
        CargoState("loaded-rail", "X", "prepared", "rail-service", "sealed"),
        CargoState("rail-arrival", "B", "in_transit", "rail-service", "sealed"),
        CargoState("done", "B", "completed", None, "delivered"),
    )
    scope = CargoDeliveryScope(
        CONTEXT,
        PROFILE.requirements_hash,
        "territory-A",
        "territory-B",
        ("A",),
        ("B",),
        START,
        START + timedelta(days=60),
        ("road", "rail", "air", "water"),
        True,
        True,
        True,
        True,
        True,
        PROOF,
    )
    by_id = {s.state_id: s for s in states}
    specifications = (
        (
            "load",
            "origin",
            "loaded-road",
            "road-service",
            "handling",
            2 * 3600,
            1,
            None,
            "initial_wait",
        ),
        (
            "road",
            "loaded-road",
            "road-arrival",
            "road-service",
            "movement",
            4 * 3600,
            3,
            "road",
            None,
        ),
        (
            "transfer",
            "road-arrival",
            "loaded-rail",
            "rail-service",
            "handling",
            3600,
            None,
            None,
            None,
        ),
        (
            "rail",
            "loaded-rail",
            "rail-arrival",
            "rail-service",
            "movement",
            12 * 3600,
            10,
            "rail",
            "transfer_wait",
        ),
        ("unload", "rail-arrival", "done", "rail-service", "handling", 2 * 3600, None, None, None),
    )
    from typing import Any, cast

    actions = tuple(
        CargoAction(
            name,
            origin,
            target,
            service,
            cast(Any, kind),
            seconds,
            START + timedelta(hours=hour) if hour is not None else None,
            cast(Any, mode),
            "required",
            receipt(by_id[origin].node_id, by_id[target].node_id, service),
            CargoWaitPolicy(
                cast(Any, waiting), receipt(by_id[origin].node_id, by_id[origin].node_id, service)
            )
            if waiting
            else None,
            True,
            PROOF,
            (),
            PROOF,
        )
        for name, origin, target, service, kind, seconds, hour, mode, waiting in specifications
    )
    ready = (CargoReadiness("origin", START, receipt("A", "A", "readiness:origin")),)
    return scope, states, actions, ready


def run_demo() -> IntercityCargoAnalysis:
    scope, states, actions, ready = demo_inputs()
    result = analyze_intercity_cargo(
        scope,
        START,
        PROFILE,
        states,
        actions,
        ready,
        config=load_freight_config(),
        rules=load_cargo_delivery_rules(),
    )
    best = result.overall.best_known
    assert best is not None and best.completed_at == START + timedelta(days=1)
    totals = {
        kind: sum(i.seconds for i in best.intervals if i.kind == kind)
        for kind in ("initial_wait", "movement", "handling", "transfer_wait")
    }
    assert totals == {
        "initial_wait": 3600,
        "movement": 57600,
        "handling": 18000,
        "transfer_wait": 7200,
    }
    assert best.evaluation.total_seconds.value == 86400 and best.evaluation.index.value == 50
    assert result.overall.optimum_proven
    return result


if __name__ == "__main__":
    result = run_demo()
    print(
        "SYNTHETIC cargo: total86400, movement57600, handling18000, waits3600+7200; score50",
        result.full_ranking_eligible,
    )
