"""Sparse physical routing boundaries retain the full scenario population/catalogue."""

import runpy
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from a2_engine import TimetableFootpath
from a2_engine.scenario.engine import (
    create_scenario_baseline,
    simulate_scenario,
    validate_scenario_result,
)
from a2_engine.scenario.identity import scenario_hash
from a2_engine.scenario.models import ScenarioInputs, ScenarioOperation
from a2_engine.scenario.overlay import execution_hash
from a2_engine.urban.transit.models import UrbanTransitNetwork
from a2_engine.urban.transit.search import search_urban_transit

DEMO: dict[str, Any] = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "examples" / "synthetic_scenario.py")
)


def sparse_inputs(*, origin: bool, destination: bool) -> tuple[ScenarioInputs, str, str]:
    inputs, incoming, outgoing = DEMO["demo_inputs"]()
    job = inputs.urban[0]
    if origin:
        job = replace(
            job,
            points=(
                replace(job.points[0], origin_node_id="unavailable-origin", access_distance_m=100),
                job.points[1],
            ),
        )
    if destination:
        missing = replace(
            job.destinations[0],
            destination_id="unavailable-clinic",
            object_identity="other-physical-object",
            physical_node_id="unavailable-destination",
        )
        job = replace(
            job,
            destinations=job.destinations + (missing,),
            coverage=tuple(
                replace(row, catalog_destination_ids=frozenset({"d", missing.destination_id}))
                for row in job.coverage
            ),
        )
    return replace(inputs, urban=(job,)), incoming, outgoing


def simulate(inputs: ScenarioInputs, incoming: str, outgoing: str) -> Any:
    original = scenario_hash(inputs)
    baseline = create_scenario_baseline(inputs)
    before = scenario_hash(baseline)
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
        DEMO["PROOF"],
    )
    result = simulate_scenario(baseline, operation)
    validate_scenario_result(baseline, operation, result)
    assert scenario_hash(inputs) == original
    assert scenario_hash(baseline) == before
    assert result.before == baseline.before
    return result


def test_missing_origin_preserves_positive_population_and_known_neighbour_effects() -> None:
    inputs, incoming, outgoing = sparse_inputs(origin=True, destination=False)
    result = simulate(inputs, incoming, outgoing)
    assert not result.complete and not result.impact.population_complete
    assert result.impact.known_affected_population == 10
    assert result.impact.unknown_cell_ids == (("city", "cell:p1", "population"),)
    assert (
        sum(point.population for point in inputs.urban[0].points if point.population is not None)
        == 100
    )
    for computation in (result.before, result.after):
        urban = computation.urban[0]
        assert "PHYSICAL_BOUNDARY_CONNECTOR_UNAVAILABLE" in urban.analysis.limitations
        mode = urban.analysis.modes[0]
        assert [point.point.point_id for point in mode.points] == ["p1", "p2"]
        assert mode.index.value is None
        missing, known = mode.points
        assert missing.point.population == 90 and missing.index.value is None
        assert known.index.value is not None
        for category in missing.categories:
            assert category.best is None and not category.minimum_proven
            assert category.index.value is None and category.count.value is None
            assert category.coverage and not category.coverage.search_complete
        assert len(urban.searches) == 2
    before = result.before.urban[0].analysis.modes[0].points[1].categories[0].best
    after = result.after.urban[0].analysis.modes[0].points[1].categories[0].best
    assert before and after
    assert (before.journey.total_seconds, after.journey.total_seconds) == (1320, 1500)


@pytest.mark.parametrize("physical", [True, False])
def test_missing_catalogue_boundary_preserves_best_known_without_minimum_or_count(
    physical: bool,
) -> None:
    inputs, incoming, outgoing = sparse_inputs(origin=False, destination=True)
    if not physical:
        job = inputs.urban[0]
        job = replace(
            job,
            destinations=tuple(
                replace(row, physical_node_id=None, access_node_id="unavailable-destination")
                if row.destination_id == "unavailable-clinic"
                else row
                for row in job.destinations
            ),
        )
        inputs = replace(inputs, urban=(job,))
    result = simulate(inputs, incoming, outgoing)
    assert len(result.overlay.inputs.urban[0].destinations) == 2
    for computation in (result.before, result.after):
        urban = computation.urban[0]
        assert len(urban.searches) == 4
        assert urban.analysis.modes[0].index.value is None
        for point in urban.analysis.modes[0].points:
            for category in point.categories:
                assert not category.minimum_proven
                assert category.index.value is None and category.count.value is None
                assert category.coverage and not category.coverage.search_complete
                assert category.coverage.catalog_destination_ids == frozenset(
                    {"d", "unavailable-clinic"}
                )
        assert urban.analysis.modes[0].points[1].categories[0].best is not None
    assert not result.complete
    assert all(effect.outcome == "unknown" for effect in result.effects)


@pytest.mark.parametrize("mode", ["walk", "public_transport"])
def test_all_boundaries_missing_cannot_certify_no_route_on_complete_timetable(mode: str) -> None:
    inputs, incoming, outgoing = sparse_inputs(origin=True, destination=False)
    job = inputs.urban[0]
    job = replace(
        job,
        modes=(mode,),
        coverage=tuple(
            replace(row, scope=replace(row.scope, passenger_mode=mode)) for row in job.coverage
        ),
        thresholds=(replace(job.thresholds[0], passenger_mode=mode),),
        points=tuple(
            replace(point, origin_node_id="missing:" + point.point_id, access_distance_m=100)
            for point in job.points
        ),
    )
    result = simulate(replace(inputs, urban=(job,)), incoming, outgoing)
    for computation in (result.before, result.after):
        assert not computation.urban[0].searches
        assert all(
            metric.state == "unknown" and metric.value is None for metric in computation.metrics
        )
    assert not result.complete and result.impact.known_affected_population == 0
    assert len(result.impact.unknown_cell_ids) == 2


@pytest.mark.parametrize("outside_service_scope", [True, False])
def test_unselected_or_outside_catalogue_does_not_reduce_completeness(
    outside_service_scope: bool,
) -> None:
    inputs, incoming, outgoing = sparse_inputs(origin=False, destination=False)
    job = inputs.urban[0]
    ignored = replace(
        job.destinations[0],
        destination_id="ignored",
        physical_node_id="unavailable-destination",
        in_service_scope=not outside_service_scope,
        categories=job.destinations[0].categories
        if outside_service_scope
        else frozenset({"adult_polyclinic"}),
    )
    result = simulate(
        replace(inputs, urban=(replace(job, destinations=job.destinations + (ignored,)),)),
        incoming,
        outgoing,
    )
    assert result.complete
    assert (
        "PHYSICAL_BOUNDARY_CONNECTOR_UNAVAILABLE" not in result.after.urban[0].analysis.limitations
    )


def test_missing_boundary_does_not_replace_unknown_population() -> None:
    inputs, incoming, outgoing = sparse_inputs(origin=True, destination=False)
    job = inputs.urban[0]
    job = replace(
        job,
        points=tuple(
            replace(point, population=None) if point.point_id == "p1" else point
            for point in job.points
        ),
        original_cells=tuple(
            replace(cell, population=None) if cell.cell_id == "cell:p1" else cell
            for cell in job.original_cells
        ),
        population_coverage=replace(
            job.population_coverage, complete=False, excluded_population=None
        ),
        districts=(),
        district_shares=(),
        district_coverage=None,
    )
    result = simulate(replace(inputs, urban=(job,)), incoming, outgoing)
    assert result.after.urban[0].analysis.modes[0].points[0].point.population is None
    assert result.impact.known_affected_population == 10
    assert result.impact.unknown_cell_ids == (("city", "cell:p1", "population"),)
    assert not result.impact.population_complete


@pytest.mark.parametrize(
    ("attack", "reason"),
    [
        ("territory", "POINT_TERRITORY_MISMATCH"),
        ("methodology", "METHODOLOGY_MISMATCH"),
        ("limit", "NONNEGATIVE_RESOURCE_LIMIT_REQUIRED"),
        ("departure", "DEPARTURE_OUTSIDE_TIMETABLE_SCOPE"),
        ("origin_is_stop", "PHYSICAL_ENDPOINT_IS_NOT_A_STOP"),
        ("destination_is_stop", "PHYSICAL_ENDPOINT_IS_NOT_A_STOP"),
        ("access_mismatch", "PHYSICAL_ACCESS_LINK_MISMATCH"),
    ],
)
def test_missing_boundaries_do_not_hide_malformed_inputs(attack: str, reason: str) -> None:
    inputs, _, _ = sparse_inputs(origin=True, destination=True)
    job, dataset = inputs.urban[0], inputs.timetables[0]
    # Every pair is skipped, so validation cannot depend on any solved pair.
    job = replace(job, destinations=(job.destinations[1],))
    if attack == "territory":
        job = replace(job, points=(replace(job.points[0], territory_id="other"), job.points[1]))
    elif attack == "methodology":
        job = replace(job, rules=replace(job.rules, methodology_version="other"))
    elif attack == "limit":
        job = replace(job, resource_limit=-1)
    elif attack == "departure":
        job = replace(
            job,
            context=replace(
                job.context,
                requested_departure_at=dataset.timetable.scope.end_at + timedelta(seconds=1),
            ),
        )
    elif attack == "origin_is_stop":
        job = replace(job, points=(replace(job.points[0], origin_node_id="S", access_node_id="X"),))
    elif attack == "destination_is_stop":
        job = replace(
            job,
            points=(job.points[0],),
            destinations=(replace(job.destinations[0], physical_node_id="D", access_node_id="X"),),
        )
    elif attack == "access_mismatch":
        table = dataset.timetable
        table = replace(
            table,
            scope=replace(table.scope, node_ids=table.scope.node_ids | {"unavailable-origin"}),
            footpaths=(
                TimetableFootpath(
                    "wrong-access", "unavailable-origin", "X", 60, True, DEMO["PROOF"]
                ),
            ),
        )
        dataset = replace(dataset, timetable=table)
    with pytest.raises(ValueError, match=reason):
        create_scenario_baseline(replace(inputs, urban=(job,), timetables=(dataset,)))


def test_direct_search_still_rejects_missing_endpoint() -> None:
    inputs, _, _ = sparse_inputs(origin=True, destination=False)
    job, table = inputs.urban[0], inputs.timetables[0].timetable
    network = UrbanTransitNetwork(job.context, table, table.scope.evidence_refs)
    with pytest.raises(ValueError, match="SEARCH_ENDPOINT_OUTSIDE_TIMETABLE_SCOPE"):
        search_urban_transit(
            network, job.points[0], job.destinations[0], category="school", rules=job.rules
        )
