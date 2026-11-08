"""Exact synthetic directed OD -> tau; no observations or deployment claim."""

import json
from datetime import UTC, date, datetime, timedelta

from a2_engine import (
    RoadBuild,
    RoadCoordinate,
    RoadEndpoint,
    RoadResponse,
    UrbanContext,
    UrbanODObservation,
    UrbanODPlan,
    UrbanODScope,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanTauDay,
    UrbanVersions,
    assemble_urban_od_week,
    calculate_urban_tau,
    load_urban_tau_rules,
    normalize_road_response,
    od_road_observation,
    od_road_request,
    plan_urban_od_week,
)


def synthetic_plan() -> tuple[
    UrbanODPlan,
    dict[str, RoadEndpoint],
    dict[str, RoadEndpoint],
    UrbanContext,
    UrbanPopulationCoverage,
]:
    build = RoadBuild(
        "snapshot",
        "source",
        "build",
        "graph",
        "profile",
        "auto",
        '{"speed_types":["freeflow"]}',
        ("synthetic-build",),
    )
    versions = UrbanVersions(
        "snapshot",
        "graph",
        "calendar",
        "population",
        "geometry",
        "a2-methodology-v3",
        "config",
        "static:build",
    )
    reference = datetime(2026, 11, 8, 12, tzinfo=UTC)
    context = UrbanContext("city", "city", reference, "static", versions, "search")
    points = tuple(
        UrbanPoint(
            key,
            "cell-" + key,
            key,
            "access-" + key,
            0,
            "city",
            weight,
            2026,
            "synthetic",
            ("synthetic-population",),
        )
        for key, weight in (("A", 90), ("B", 10))
    )
    scope = UrbanODScope(
        "city", "private_car", versions, "static", "search", "UTC", frozenset({"A", "B"}), 14400
    )
    start = date(2026, 10, 26)
    days = tuple(
        UrbanTauDay(start + timedelta(days=i), True, False, True, True, ("synthetic-calendar",))
        for i in range(7)
    )
    plan = plan_urban_od_week(
        scope,
        points,
        week_start=start,
        reference_at=reference,
        calendar=days,
        rules=load_urban_tau_rules(),
        profile_hash=build.profile_hash,
        evidence_refs=("synthetic-scope",),
    )
    entries = {
        p.point_id: RoadEndpoint(
            build,
            p.origin_node_id,
            p.access_node_id,
            RoadCoordinate(20, 54),
            "correlation-" + p.point_id,
            "entry",
            0,
            ("synthetic-zero-entry",),
        )
        for p in points
    }
    exits = {
        key: RoadEndpoint(
            build,
            e.physical_node_id,
            e.access_node_id,
            e.coordinate,
            e.correlation_id,
            "exit",
            0,
            ("synthetic-zero-exit",),
        )
        for key, e in entries.items()
    }
    population = UrbanPopulationCoverage(
        context, True, 0, ("synthetic-population",), scope.point_ids
    )
    return plan, entries, exits, context, population


def synthetic_observations(
    plan: UrbanODPlan,
    entries: dict[str, RoadEndpoint],
    exits: dict[str, RoadEndpoint],
    *,
    two_stage: bool = False,
) -> tuple[UrbanODObservation, ...]:
    observations = []
    for batch in plan.batches(7):
        for task in batch:
            request = od_road_request(task, entries, exits)
            late = task.departure_at in plan.departures[14:]
            seconds = (
                (3600 if late and two_stage else 600)
                if task.origin.point_id == "A"
                else (1200 if two_stage else 1800)
            )
            unreachable = two_stage and late and task.origin.point_id == "B"
            body = (
                {"id": request.request_id, "error_code": 442}
                if unreachable
                else {
                    "id": request.request_id,
                    "trip": {
                        "status": 0,
                        "units": "kilometers",
                        "locations": [
                            dict(lon=e.coordinate.lon, lat=e.coordinate.lat)
                            for e in (request.origin, request.destination)
                        ],
                        "summary": {"time": seconds, "length": 1, "has_ferry": False},
                        "legs": [
                            {
                                "summary": {"time": seconds, "length": 1, "has_ferry": False},
                                "maneuvers": [{"travel_mode": "drive"}],
                            }
                        ],
                    },
                }
            )
            response = RoadResponse(
                request.request_id,
                request.payload_hash,
                request.origin.build.profile_hash,
                "response",
                json.dumps(body),
                ("synthetic-response",),
                request.origin.coordinate,
                request.destination.coordinate,
                ("synthetic-endpoints",),
                ("synthetic-minimum",),
                ("synthetic-exhaustive-unreachable",) if unreachable else (),
                True,
            )
            observations.append(
                od_road_observation(task, normalize_road_response(request, response))
            )
    return tuple(observations)


def main() -> None:
    plan, entries, exits, context, population = synthetic_plan()
    for two_stage, expected in ((False, 1200), (True, 2250)):
        rows = synthetic_observations(plan, entries, exits, two_stage=two_stage)
        week = assemble_urban_od_week(plan, reversed(rows))
        result = calculate_urban_tau(
            context,
            plan.points,
            population,
            (week,),
            passenger_mode="private_car",
            timezone="UTC",
            reference_at=plan.reference_at,
            rules=load_urban_tau_rules(),
        )
        assert result.tau_seconds.value == expected, result.reasons
        assert result.to_threshold().seconds == expected
        print(f"SYNTHETIC 28 slices, directed pairs: tau={expected}s")


if __name__ == "__main__":
    main()
