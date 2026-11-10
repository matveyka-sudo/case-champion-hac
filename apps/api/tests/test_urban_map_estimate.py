"""TEST_ONLY accepted map facts and route estimates separate from minimum indices."""

from dataclasses import replace

import pytest
from a2_api.adapters import product
from a2_api.adapters.product_projection import Projection, project_urban
from a2_api.adapters.urban_map import accepted_urban_map
from a2_api.core.errors import DomainError
from a2_api.db.validation import _validate_scenario_semantics
from a2_contracts.v2.dataset import AcceptedData
from a2_engine import MetricResult, category_index
from test_product_runtime import claim_for, configure, request_for
from test_product_runtime import transport_facts as transport_facts
from test_product_scenario_publication import queued_case
from test_product_scenarios import (
    make_case,
    partial_timetable_case,
    run,
    trusted_urban_scope,
    with_claim,
)

OBSERVED = "observed_route_accessibility"


def test_generic_accepted_input_map_keeps_original_locations_population_and_missing_shapes(
    tmp_path, monkeypatch, transport_facts
):
    snapshot, _, _ = transport_facts
    configure(tmp_path, monkeypatch, transport_facts)
    before = snapshot.model_dump_json()
    result = product.calculate(claim_for(snapshot, request_for(snapshot)))
    mapped = result.map_data
    assert mapped.status == "available" and mapped.completeness == "partial"
    assert mapped.reason_code == "PARTIAL_URBAN_MAP"
    cells = {row.cell_id: row for row in snapshot.population_cells}
    points = {row.point_id: row for row in snapshot.sample_points}
    destinations = {row.destination_id: row for row in snapshot.destinations}
    for feature in mapped.features:
        if feature.entity_kind == "cell":
            original = cells[feature.cell_id]
            assert feature.geometry is None and feature.geometry_kind == "none"
            assert feature.geometry_ref == original.geometry.geometry_id
        elif feature.entity_kind == "sample_point":
            point = points[feature.point_id]
            assert feature.geometry.coordinates == (point.location.lon, point.location.lat)
            original = cells[point.cell_id]
        else:
            original = destinations[feature.entity_id]
            assert feature.entity_kind == "destination"
            assert feature.geometry.coordinates == (original.location.lon, original.location.lat)
            assert set(feature.categories) <= set(result.request.categories)
            continue
        assert (feature.population, feature.population_year, feature.population_method) == (
            original.population,
            original.population_year,
            original.population_method,
        )
    assert snapshot.model_dump_json() == before
    assert all(leg.geometry is None for journey in result.journeys for leg in journey.legs)


def test_unknown_population_remains_unknown_on_both_cell_and_point(transport_facts):
    snapshot = transport_facts[0]
    payload = snapshot.model_dump(mode="json")
    cell_id = payload["sample_points"][0]["cell_id"]
    for cell in payload["population_cells"]:
        if cell["cell_id"] == cell_id:
            cell.update(population=None, population_year=None, population_method=None)
    snapshot = AcceptedData.model_validate(payload)
    mapped = accepted_urban_map(snapshot, request_for(snapshot))
    selected = [feature for feature in mapped.features if feature.cell_id == cell_id]
    assert {feature.entity_kind for feature in selected} == {"cell", "sample_point"}
    assert all(feature.population is None for feature in selected)


@pytest.mark.parametrize(
    "change", [{"city_id": "TEST_ONLY-foreign"}, {"snapshot_id": "foreign"}, {"area_id": "foreign"}]
)
def test_generic_map_rejects_other_source_scope(transport_facts, change):
    snapshot = transport_facts[0]
    with pytest.raises(ValueError, match="SOURCE_SCOPE"):
        accepted_urban_map(snapshot, request_for(snapshot).model_copy(update=change))


def test_pt_estimate_uses_existing_time_and_h_and_keeps_unknown_minimum():
    case = partial_timetable_case()
    baseline = case[3]
    metrics_before = baseline.metrics.copy()
    estimates = [score for score in baseline.scores if score.name == OBSERVED]
    assert estimates and any(score.value is not None for score in estimates)
    by_id = {metric.metric_id: metric for metric in baseline.metrics}
    for score in estimates:
        assert score.completeness == "partial" and not score.ranking_eligible
        time, h = (by_id[component.id] for component in score.components)
        assert time.name == "minimum_journey_seconds" and h.name == "urban_h_seconds"
        assert score.value == category_index(time.value, h.value).value
        if score.value is not None:
            assert score.reason_code == "MINIMUM_NOT_PROVEN"
            assert time.journey_id in {journey.journey_id for journey in baseline.journeys}
    assert all(score.value is None for score in baseline.scores if score.name != OBSERVED)
    assert baseline.metrics == metrics_before
    candidate, _ = run(case)
    assert (
        candidate.before.metrics == baseline.metrics and candidate.before.scores == baseline.scores
    )
    assert "HYPOTHETICAL_SCENARIO_ROUTE_ESTIMATE" in candidate.limitations
    assert all(not score.ranking_eligible for score in candidate.after.scores)


def test_unavailable_h_does_not_receive_an_estimated_score():
    context, _, _, baseline, frozen = partial_timetable_case()
    analysis = replace(
        frozen.before.urban[0].analysis, h_seconds=MetricResult(None, "unknown", "H_UNAVAILABLE")
    )
    output = Projection(context, baseline.evidence_refs)
    project_urban(output, analysis)
    estimates = [score for score in output.finish().scores if score.name == OBSERVED]
    assert estimates and all(score.value is None for score in estimates)
    assert any(score.reason_code == "H_UNAVAILABLE" for score in estimates)


@pytest.mark.asyncio
async def test_estimate_pg_roundtrip_preserves_exact_before_context_and_saved_proposal(database):
    pool, _, _ = database
    case = make_case(unknown_population=True)
    repository, claim, candidate, baseline = await queued_case(pool, case)
    baseline_bytes = baseline.model_dump_json()
    proposal_bytes = claim.context.scenario_proposal.model_dump_json()
    assert any(score.name == OBSERVED and score.value is not None for score in baseline.scores)
    territory = next(score for score in baseline.scores if score.name == "territory_index")
    assert territory.value is None and territory.aggregation_basis.unknown_positive_weight
    assert candidate.affected_population is None
    await repository.succeed(claim, candidate)
    saved = await repository.get_run(candidate.run_id, claim.context.access_scope)
    assert saved.before == candidate.before and saved.after == candidate.after
    assert (
        await repository.get_run(baseline.analysis_id, claim.context.access_scope)
    ).model_dump_json() == baseline_bytes
    parameters = await repository.get_run_parameters(saved.run_id, claim.context.access_scope)
    assert parameters["context"] == claim.context.model_dump(mode="json")
    assert claim.context.scenario_proposal.model_dump_json() == proposal_bytes


def test_new_estimates_do_not_weaken_exact_saved_before_or_h_guards():
    case = partial_timetable_case()
    claim, _, _, baseline, _ = with_claim(case)
    candidate, _ = run(case)
    scope = trusted_urban_scope(case)
    observed = next(score for score in candidate.before.scores if score.name == OBSERVED)
    changed = observed.model_copy(update={"value": 99.0})
    before = candidate.before.model_copy(
        update={
            "scores": [
                changed if score.score_id == changed.score_id else score
                for score in candidate.before.scores
            ]
        }
    )
    with pytest.raises(DomainError, match="before differs"):
        _validate_scenario_semantics(
            candidate.model_copy(update={"before": before}),
            baseline,
            claim.context.scenario_proposal,
            endpoint_scope=scope,
            expected_snapshot_hash=scope.snapshot_hash,
        )
    h = next(metric for metric in candidate.after.metrics if metric.name == "urban_h_seconds")
    after = candidate.after.model_copy(
        update={
            "metrics": [
                h.model_copy(update={"value": 1.0}) if metric.metric_id == h.metric_id else metric
                for metric in candidate.after.metrics
            ]
        }
    )
    with pytest.raises(DomainError, match="area scale"):
        _validate_scenario_semantics(
            candidate.model_copy(update={"after": after}),
            baseline,
            claim.context.scenario_proposal,
            endpoint_scope=scope,
            expected_snapshot_hash=scope.snapshot_hash,
        )
