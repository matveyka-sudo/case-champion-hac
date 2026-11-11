"""Finite truck access and a scheduled mixed-mode delivery with real handling."""

from typing import Literal

from a2_engine import load_cargo_delivery_rules, load_freight_config
from a2_engine.freight.delivery.models import (
    CargoAction,
    CargoDeliveryScope,
    CargoReadiness,
    CargoState,
    CargoWaitPolicy,
)
from a2_engine.freight.models import (
    CargoCompatibility,
    CargoContext,
    CargoProfile,
    CargoTemperature,
    CargoVehicle,
)
from a2_engine.freight.truck import TruckRoute, TruckSearchReceipt
from a2_engine.freight.urban_analysis import CargoNode, CargoSite, UrbanCargoCoverage
from a2_engine.scenario.models import (
    CargoExecution,
    CargoScenarioInput,
    ScenarioBaseline,
    ScenarioInputs,
    ScenarioOperation,
    UrbanCargoScenarioInput,
)
from a2_engine.scenario.overlay import execution_hash
from a2_engine.shared.normalized import content_hash

from ml1_demo.model import CONFIG_PATH, DAY, PROOF, START, VERSION, City, clock

Compatibility = Literal["confirmed", "forbidden", "unknown"]
Mode = Literal["road", "rail", "air", "water"]
Urgency = Literal["urgent", "ordinary", "nonurgent"]
URGENCIES: tuple[Urgency, ...] = ("urgent", "ordinary", "nonurgent")


def cargo_context(snapshot: str) -> CargoContext:
    return CargoContext(
        snapshot,
        f"{snapshot}:graph",
        f"{snapshot}:build",
        f"{snapshot}:calendar",
        f"{snapshot}:rules",
        f"{VERSION}:truck",
        "a2-methodology-v3",
    )


def cargo_profile(urgency: Urgency) -> CargoProfile:
    return CargoProfile(
        "sealed-food",
        VERSION,
        "food",
        urgency,
        CargoTemperature(False, None, None, "ambient"),
        ("sealed",),
        ("freight-entry",),
        CargoVehicle("truck", 12000, 3, 2.5, 8, "freight"),
    )


def compatibility(
    context: CargoContext,
    profile: CargoProfile,
    origin: str,
    destination: str,
    service: str,
    direction: Literal["outbound", "delivery"],
    state: Compatibility = "confirmed",
) -> CargoCompatibility:
    return CargoCompatibility(
        f"receipt:{service}:{origin}:{destination}",
        context,
        profile.profile_id,
        profile.version,
        profile.requirements_hash,
        origin,
        destination,
        service,
        direction,
        clock(-86400),
        clock(90 * 86400),
        state,
        PROOF,
    )


def urban_cargo_input(city: City) -> UrbanCargoScenarioInput:
    context, profile = cargo_context(f"{city.snapshot}:freight"), cargo_profile("ordinary")
    sites = tuple(CargoSite(f"warehouse-{i}", context, PROOF) for i in range(3))
    nodes = tuple(
        CargoNode(f"terminal-{j}", "logistics_terminal", "terminal", context, PROOF)
        for j in range(2)
    )
    routes = tuple(
        TruckRoute(
            f"access:{i}:{j}",
            context,
            profile.requirements_hash,
            profile.vehicle,
            site.site_id,
            node.node_id,
            f"truck:{i}:{j}",
            "outbound",
            START,
            900 + 120 * city.number + 180 * i - 180 * j,
            60,
            90,
            "reachable",
            compatibility(
                context,
                profile,
                site.site_id,
                node.node_id,
                f"truck:{i}:{j}",
                "outbound",
                "forbidden" if j else "confirmed",
            ),
            PROOF,
        )
        for i, site in enumerate(sites)
        for j, node in enumerate(nodes)
    )
    receipts = tuple(
        TruckSearchReceipt(
            r.route_id,
            r.fingerprint,
            content_hash(
                (context, profile.requirements_hash, r.from_id, r.to_id, START, "outbound")
            ),
            True,
            True,
            PROOF,
        )
        for r in routes
    )
    return UrbanCargoScenarioInput(
        f"{city.snapshot}:urban-cargo",
        profile,
        START,
        sites,
        nodes,
        routes,
        receipts,
        UrbanCargoCoverage(
            context,
            profile.requirements_hash,
            "outbound",
            tuple(s.site_id for s in sites),
            ("terminal",),
            True,
            PROOF,
        ),
        ("terminal",),
        city.area,
        load_freight_config(CONFIG_PATH),
    )


def delivery_inputs(origin: City, destination: City) -> ScenarioInputs:
    n = 3 * origin.number + destination.number
    snapshot = f"{VERSION}:delivery:{origin.id}:{destination.id}"
    ctx, profile = cargo_context(snapshot), cargo_profile(URGENCIES[(n - 1) % 3])
    a, x, b = f"{origin.id}:warehouse", "freight-hub", f"{destination.id}:warehouse"
    road_dep = 10800
    road_seconds = 14400 + 600 * n
    rail_seconds = 43200 + 1200 * n
    transshipment = 3600 + 300 * n
    rail_dep = road_dep + road_seconds + transshipment + 7200
    states = (
        CargoState("origin", a, "unprepared", None, "ambient"),
        CargoState("loaded-road", a, "prepared", "road-service", "sealed"),
        CargoState("road-arrival", x, "in_transit", "road-service", "sealed"),
        CargoState("loaded-rail", x, "prepared", "rail-service", "sealed"),
        CargoState("rail-arrival", b, "in_transit", "rail-service", "sealed"),
        CargoState("done", b, "completed", None, "delivered"),
    )
    by_id = {s.state_id: s for s in states}

    def action(
        name: str,
        source: str,
        target: str,
        service: str,
        seconds: int,
        at: int | None = None,
        mode: Mode | None = None,
        waiting: Literal["initial_wait", "transfer_wait"] | None = None,
    ) -> CargoAction:
        start, end = by_id[source].node_id, by_id[target].node_id
        return CargoAction(
            name,
            source,
            target,
            service,
            "movement" if mode else "handling",
            seconds,
            clock(at) if at is not None else None,
            mode,
            "required",
            compatibility(ctx, profile, start, end, service, "delivery"),
            CargoWaitPolicy(waiting, compatibility(ctx, profile, start, start, service, "delivery"))
            if waiting
            else None,
            True,
            PROOF,
            (),
            PROOF,
        )

    actions = (
        action("load", "origin", "loaded-road", "road-service", 7200, 3600, waiting="initial_wait"),
        action(
            "road", "loaded-road", "road-arrival", "road-service", road_seconds, road_dep, "road"
        ),
        action("transfer", "road-arrival", "loaded-rail", "rail-service", transshipment),
        action(
            "rail",
            "loaded-rail",
            "rail-arrival",
            "rail-service",
            rail_seconds,
            rail_dep,
            "rail",
            "transfer_wait",
        ),
        action("unload", "rail-arrival", "done", "rail-service", 7200),
    )
    scope = CargoDeliveryScope(
        ctx,
        profile.requirements_hash,
        origin.id,
        destination.id,
        (a,),
        (b,),
        START,
        clock(60 * 86400),
        ("road", "rail"),
        True,
        True,
        True,
        True,
        True,
        PROOF,
    )
    ready = (
        CargoReadiness(
            "origin", START, compatibility(ctx, profile, a, a, "readiness:origin", "delivery")
        ),
    )
    executions = tuple(
        CargoExecution(s, DAY, tuple(a.action_id for a in actions if a.service_id == s), PROOF)
        for s in ("road-service", "rail-service")
    )
    job = CargoScenarioInput(
        snapshot,
        scope,
        START,
        profile,
        states,
        actions,
        ready,
        executions,
        load_freight_config(CONFIG_PATH),
        load_cargo_delivery_rules(CONFIG_PATH),
        physical_sites=(CargoSite(a, ctx, PROOF),),
    )
    return ScenarioInputs(cargo=(job,), urban_cargo=(urban_cargo_input(origin),))


def delivery_operation(baseline: ScenarioBaseline) -> ScenarioOperation:
    job = baseline.inputs.cargo[0]
    return ScenarioOperation(
        baseline.baseline_id,
        f"{job.job_id}:coordinate",
        "coordinate_transfer",
        "cargo",
        job.job_id,
        "rail-service",
        DAY,
        "rail",
        execution_hash(baseline, "cargo", job.job_id, "rail-service"),
        -3600,
        "road-service",
        "road",
        PROOF,
    )
