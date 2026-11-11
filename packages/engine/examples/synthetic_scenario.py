"""Real route search before/after an immutable, allowlisted transfer adjustment."""

from datetime import UTC, date, datetime, timedelta

from a2_engine import (
    Timetable,
    TimetableEvent,
    TimetableRun,
    TimetableScope,
    TimetableTransfer,
    UrbanArea,
    UrbanContext,
    UrbanCoverage,
    UrbanDestination,
    UrbanDistrict,
    UrbanDistrictCoverage,
    UrbanDistrictShare,
    UrbanOriginalCell,
    UrbanPoint,
    UrbanPopulationCoverage,
    UrbanSearchScope,
    UrbanThreshold,
    UrbanVersions,
    load_urban_config,
    load_urban_journey_rules,
)
from a2_engine.scenario.engine import create_scenario_baseline, simulate_scenario
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import (
    ScenarioBaseline,
    ScenarioInputs,
    ScenarioOperation,
    ScenarioResult,
    ScenarioTimetable,
    UrbanScenarioInput,
)
from a2_engine.scenario.overlay import execution_hash

START = datetime(2026, 11, 9, 8, tzinfo=UTC)
PROOF = ("synthetic-scenario-proof",)


def demo_inputs() -> tuple[ScenarioInputs, str, str]:
    config = load_urban_config()
    rules = load_urban_journey_rules()

    def run(name: str, a: str, dep: int, b: str, arr: int) -> TimetableRun:
        return TimetableRun(
            "snapshot",
            "synthetic",
            name,
            date(2026, 11, 9),
            "UTC",
            "bus",
            (
                TimetableEvent(
                    0,
                    a,
                    START + timedelta(seconds=dep),
                    START + timedelta(seconds=dep),
                    True,
                    True,
                    PROOF,
                ),
                TimetableEvent(
                    1,
                    b,
                    START + timedelta(seconds=arr),
                    START + timedelta(seconds=arr),
                    True,
                    True,
                    PROOF,
                ),
            ),
            True,
            PROOF,
        )

    incoming = run("incoming", "S", 0, "X", 600)
    outgoing = run("outgoing", "X", 720, "D", 1320)
    timetable = Timetable(
        TimetableScope(
            "snapshot",
            "graph",
            "calendar",
            "timetable-v1",
            "footpaths",
            "transfers",
            "UTC",
            START - timedelta(hours=1),
            START + timedelta(days=9),
            frozenset({"S", "X", "D"}),
            True,
            True,
            True,
            True,
            True,
            PROOF,
        ),
        (incoming, outgoing),
        (),
        (
            TimetableTransfer(
                "connection", "X", "X", 300, True, PROOF, incoming.run_id, outgoing.run_id
            ),
        ),
    )
    versions = UrbanVersions(
        "snapshot",
        "graph",
        "calendar",
        "population",
        "geometry",
        config.methodology_version,
        "fixed-config",
        "fixed-traffic",
    )
    context = UrbanContext("city", "city", START, "static", versions, "same-search")
    points = tuple(
        UrbanPoint(
            name, "cell:" + name, node, node, 0, "city", population, 2026, "synthetic", PROOF
        )
        for name, node, population in (("p1", "S", 90), ("p2", "X", 10))
    )
    destinations = (
        UrbanDestination(
            "d", "physical-object", frozenset({"school", "airport"}), "D", "city", True, True, PROOF
        ),
    )
    coverage = tuple(
        UrbanCoverage(
            UrbanSearchScope(
                context, p.point_id, category, "public_transport", rules.max_total_wait_seconds
            ),
            True,
            True,
            PROOF,
            frozenset({"d"}),
        )
        for p in points
        for category in ("school", "airport")
    )
    threshold = UrbanThreshold(
        "city", "public_transport", versions, "static", "same-search", 1800, PROOF
    )
    cells = tuple(
        UrbanOriginalCell(
            p.cell_id, "city", "grid", p.population, 2026, "synthetic", p.point_id, False, PROOF
        )
        for p in points
    )
    district = UrbanDistrict("district", "city", "District", "boundary-v1", PROOF)
    district_coverage = UrbanDistrictCoverage(
        context,
        "district-catalogue",
        "distribution",
        frozenset({"grid"}),
        {"district": "boundary-v1"},
        frozenset({"district"}),
        frozenset(p.cell_id for p in points),
        frozenset({"district"}),
        True,
        PROOF,
    )
    urban = UrbanScenarioInput(
        "urban",
        "network",
        context,
        points,
        destinations,
        coverage,
        UrbanArea("city", 100, "geometry", PROOF),
        (("school", "low"), ("airport", "low")),
        ("public_transport",),
        UrbanPopulationCoverage(context, True, 0, PROOF, frozenset(p.point_id for p in points)),
        config,
        rules,
        thresholds=(threshold,),
        original_cells=cells,
        districts=(district,),
        district_shares=tuple(
            UrbanDistrictShare(p.cell_id, "district", p.population, PROOF) for p in points
        ),
        district_coverage=district_coverage,
    )
    return (
        ScenarioInputs(timetables=(ScenarioTimetable("network", timetable),), urban=(urban,)),
        incoming.run_id,
        outgoing.run_id,
    )


def demo_baseline() -> tuple[ScenarioBaseline, ScenarioOperation]:
    inputs, incoming, outgoing = demo_inputs()
    baseline = create_scenario_baseline(inputs)
    operation = ScenarioOperation(
        baseline.baseline_id,
        "coordinate-X",
        "coordinate_transfer",
        "timetable",
        "network",
        outgoing,
        date(2026, 11, 9),
        "0",
        execution_hash(baseline, "timetable", "network", outgoing),
        180,
        incoming,
        "1",
        PROOF,
    )
    return baseline, operation


def run_demo() -> ScenarioResult:
    baseline, operation = demo_baseline()
    original = scenario_hash(baseline)
    result = simulate_scenario(baseline, operation)
    assert scenario_hash(baseline) == original
    assert result.feasibility.state == "unverified"
    assert result.complete
    assert result.impact.known_affected_population == 100
    assert len(result.impact.affected_cell_ids) == 2
    assert any(e.outcome == "new_connection" for e in result.effects)
    assert any(e.outcome == "worsened" and e.key[2] == "p2" for e in result.effects)
    assert result.after.urban[0].analysis.h_seconds == baseline.before.urban[0].analysis.h_seconds
    assert result.after.urban[0].analysis.modes[0].points[0].categories[0].best is not None
    assert simulate_scenario(baseline, operation) == result
    return result


if __name__ == "__main__":
    result = run_demo()
    print(
        "SYNTHETIC scenario:",
        [(e.key, e.outcome, e.delta.value) for e in result.effects if e.outcome != "unchanged"],
        "affected population=",
        result.impact.known_affected_population,
        "feasibility=",
        result.feasibility.state,
    )
