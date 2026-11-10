"""Synthetic timetable -> forward analysis -> latest return. No HTTP or GTFS ingest."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from a2_engine import (
    Timetable,
    TimetableEvent,
    TimetableFootpath,
    TimetableRun,
    TimetableScope,
    UrbanAnalysis,
    UrbanArea,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanLastReturnResult,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanTransitNetwork,
    UrbanTransitResult,
    UrbanVersions,
    analyze_urban_candidates,
    load_urban_config,
    load_urban_journey_rules,
    search_urban_last_return,
    search_urban_transit,
)


def run_demo() -> tuple[UrbanTransitResult, UrbanAnalysis, UrbanLastReturnResult]:
    zone = ZoneInfo("Europe/Kaliningrad")
    proof = ("synthetic-timetable-proof",)

    def instant(day: int, hour: int, minute: int = 0) -> datetime:
        return datetime(2026, 11, day, hour, minute, tzinfo=zone).astimezone(UTC)

    def service(
        name: str, start_node: str, start: datetime, end_node: str, end: datetime
    ) -> TimetableRun:
        return TimetableRun(
            "synthetic-snapshot",
            "synthetic-source",
            name,
            date(2026, 11, 8),
            zone.key,
            "bus",
            (
                TimetableEvent(10, start_node, start, start, True, True, proof),
                TimetableEvent(30, end_node, end, end, True, True, proof),
            ),
            True,
            proof,
        )

    runs = (
        service("outbound", "S", instant(8, 8, 12), "T", instant(8, 8, 27)),
        service("return-A", "A", instant(8, 23, 55), "G", instant(9, 0, 15)),
        service("return-B", "B", instant(8, 23, 50), "G", instant(9, 0, 10)),
    )
    walks = tuple(
        TimetableFootpath(a + "-" + b, a, b, seconds, True, proof)
        for a, b, seconds in [
            ("O", "S", 300),
            ("T", "D", 300),
            ("D", "A", 1200),
            ("D", "B", 300),
            ("G", "O", 300),
        ]
    )
    scope = TimetableScope(
        "synthetic-snapshot",
        "synthetic-graph",
        "synthetic-calendar",
        "synthetic-timetable-v1",
        "synthetic-walk-v1",
        "synthetic-transfer-v1",
        zone.key,
        instant(8, 0),
        instant(10, 0),
        frozenset({"O", "S", "T", "D", "A", "B", "G"}),
        True,
        True,
        True,
        True,
        True,
        proof,
    )
    timetable = Timetable(scope, runs, walks, ())
    config = load_urban_config()
    rules = load_urban_journey_rules()
    versions = UrbanVersions(
        scope.snapshot_id,
        scope.graph_version,
        scope.calendar_version,
        "synthetic-population",
        "synthetic-geometry",
        config.methodology_version,
        "synthetic-config",
        "synthetic-static",
    )
    context = UrbanContext(
        "synthetic-city", "synthetic-city", instant(8, 8), "static", versions, "synthetic-search"
    )
    network = UrbanTransitNetwork(context, timetable, proof)
    point = UrbanPoint(
        "p", "cell", "O", "O", 0, context.city_id, 100, 2026, "synthetic-estimate", proof
    )
    destination = UrbanDestination(
        "d", "physical-object", frozenset({"airport"}), "D", context.city_id, True, True, proof
    )
    outbound = search_urban_transit(network, point, destination, category="airport", rules=rules)
    assert outbound.best_known is not None and outbound.search_complete
    assert outbound.best_known.total_seconds == 1920
    assert outbound.best_known.total_wait_seconds == 420
    assert outbound.best_known.waiting_share.value == 0.21875
    # Institutional catalog completeness is a separate accepted input, not inferred from routing.
    coverage = UrbanCoverage(
        UrbanSearchScope(context, "p", "airport", "public_transport", rules.max_total_wait_seconds),
        True,
        outbound.search_complete,
        proof,
        frozenset({"d"}),
    )
    analysis = analyze_urban_candidates(
        context,
        (point,),
        (destination,),
        (outbound.best_known.candidate,),
        (coverage,),
        area=UrbanArea(context.city_id, 224.668, versions.geometry_version, proof),
        selected_categories={"airport": "high"},
        modes=("public_transport",),
        population_coverage=UrbanPopulationCoverage(context, True, 0, proof, frozenset({"p"})),
        config=config,
        rules=rules,
    )
    chosen = analysis.modes[0].points[0].categories[0].best
    assert chosen is not None
    last = search_urban_last_return(
        network, point, chosen.destination, local_day=date(2026, 11, 8), rules=rules
    )
    assert last.status == "available"
    assert (last.leave_at, last.first_departure_at, last.arrival_at) == (
        instant(8, 23, 45),
        instant(8, 23, 50),
        instant(9, 0, 15),
    )
    return outbound, analysis, last


if __name__ == "__main__":
    outbound, analysis, last = run_demo()
    print(
        "SYNTHETIC: total=",
        outbound.seconds.value,
        "wait=",
        outbound.best_known.total_wait_seconds if outbound.best_known else None,
        "index=",
        analysis.modes[0].index.value,
        "count=",
        analysis.modes[0].points[0].categories[0].count.status,
    )
    print("Return (UTC):", last.leave_at, last.first_departure_at, last.arrival_at)
