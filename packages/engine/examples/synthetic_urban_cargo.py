"""Synthetic physical sites and evidence-bound truck minima, no passenger auto."""

from datetime import UTC, datetime, timedelta

from a2_engine.freight.models import CargoCompatibility, CargoContext, CargoProfile, CargoVehicle
from a2_engine.freight.truck import TruckRoute, TruckSearchReceipt
from a2_engine.freight.urban_analysis import (
    CargoNode,
    CargoSite,
    UrbanCargoAnalysis,
    UrbanCargoCoverage,
    analyze_urban_cargo,
)
from a2_engine.shared.config import load_freight_config
from a2_engine.shared.normalized import content_hash

START = datetime(2026, 11, 9, tzinfo=UTC)
PROOF = ("synthetic-proof",)
CONTEXT = CargoContext(
    "snapshot", "truck-graph", "truck-build", "calendar", "rules", "truck-v1", "a2-methodology-v3"
)
PROFILE = CargoProfile(
    "profile",
    "v1",
    "food",
    "ordinary",
    "ambient",
    ("sealed",),
    ("freight-entry",),
    CargoVehicle("truck", 12000, 3, 2.5, 8, "freight"),
)


def compatibility(
    from_id: str, to_id: str, service: str, direction: str = "outbound"
) -> CargoCompatibility:
    from typing import Literal, cast

    return CargoCompatibility(
        "receipt:" + service,
        CONTEXT,
        PROFILE.profile_id,
        PROFILE.version,
        PROFILE.requirements_hash,
        from_id,
        to_id,
        service,
        cast(Literal["inbound", "outbound", "delivery"], direction),
        START - timedelta(days=1),
        START + timedelta(days=90),
        "confirmed",
        PROOF,
    )


def truck(
    site: str, node: str, seconds: float, *, inbound: bool = False
) -> tuple[TruckRoute, TruckSearchReceipt]:
    origin, target = (node, site) if inbound else (site, node)
    direction = "inbound" if inbound else "outbound"
    from typing import Literal, cast

    route = TruckRoute(
        site + ":" + node,
        CONTEXT,
        PROFILE.requirements_hash,
        PROFILE.vehicle,
        origin,
        target,
        "truck:" + site + ":" + node,
        cast(Literal["inbound", "outbound"], direction),
        START,
        seconds - 60,
        30,
        30,
        "reachable",
        compatibility(origin, target, "truck:" + site + ":" + node, direction),
        PROOF,
    )
    scope_id = content_hash((CONTEXT, PROFILE.requirements_hash, origin, target, START, direction))
    receipt = TruckSearchReceipt(route.route_id, route.fingerprint, scope_id, True, True, PROOF)
    return route, receipt


def run_demo() -> UrbanCargoAnalysis:
    sites = (CargoSite("site1", CONTEXT, PROOF), CargoSite("site2", CONTEXT, PROOF))
    nodes = (CargoNode("port", "port", "port", CONTEXT, PROOF),)
    pairs = (truck("site1", "port", 1200), truck("site2", "port", 2400))
    coverage = UrbanCargoCoverage(
        CONTEXT, PROFILE.requirements_hash, "outbound", ("site1", "site2"), ("port",), True, PROOF
    )
    result = analyze_urban_cargo(
        PROFILE,
        START,
        sites,
        nodes,
        tuple(p[0] for p in pairs),
        tuple(p[1] for p in pairs),
        coverage,
        ("port",),
        100,
        config=load_freight_config(),
    )
    assert [s.evaluation.index.value for s in result.by_type[0].sites] == [50, 25]
    assert result.by_type[0].index.value == 37.5 and result.by_type[0].full_ranking_eligible
    return result


if __name__ == "__main__":
    print("SYNTHETIC urban cargo:", run_demo().by_type[0].index.value)
