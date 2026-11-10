"""TEST_ONLY: DEMO disclosure is saved with canonical computed output."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
from a2_api.adapters import product
from a2_api.adapters.demo_map import attach_demo_result
from a2_api.adapters.demo_transport import DEMO_LIMITATIONS
from a2_api.adapters.product_runtime import FileRef
from a2_contracts.common import canonical_hash
from test_demo_transport import CLOCK
from test_demo_transport import demo as demo
from test_product_runtime import claim_for, request_for
from test_product_runtime import transport_facts as transport_facts


def test_actual_product_output_keeps_model_labels_map_and_source_population(
    transport_facts, demo, monkeypatch
):
    snapshot, table, _ = demo
    _, facts, profile = transport_facts
    context = replace(
        facts.context,
        versions=replace(facts.context.versions, snapshot_id=snapshot.snapshot_id),
    )
    facts = facts.model_copy(
        update={
            "context": context,
            "population_coverage": replace(facts.population_coverage, context=context),
            "coverage": tuple(
                replace(row, scope=replace(row.scope, context=context)) for row in facts.coverage
            ),
            "timetable": table,
            "timetable_service_clock": CLOCK,
        }
    )
    profile = profile.model_copy(
        update={"demo_transport": FileRef(path="TEST_ONLY-receipt.json", sha256="0" * 64)}
    )
    runtime = SimpleNamespace(
        digest="TEST_ONLY",
        snapshots={snapshot.snapshot_id: snapshot},
        select=lambda request, accepted: (profile, facts),
        scenario_documents={},
    )
    monkeypatch.setenv("A2_CODE_VERSION", "TEST_ONLY")
    monkeypatch.setattr(product, "load_runtime", lambda: runtime)
    result = product.calculate(claim_for(snapshot, request_for(snapshot)))
    assert DEMO_LIMITATIONS <= set(result.limitations)
    assert all(not score.ranking_eligible for score in result.scores)
    assert result.result_hash == canonical_hash(
        result.model_dump(mode="json", exclude={"result_hash"})
    )
    points = [f for f in result.map_data.features if f.entity_kind == "sample_point"]
    assert len(points) == len(snapshot.sample_points)
    assert points[0].geometry.coordinates == (
        snapshot.sample_points[0].location.lon,
        snapshot.sample_points[0].location.lat,
    )
    cells = {cell.cell_id: cell for cell in snapshot.population_cells}
    assert points[0].population == cells[snapshot.sample_points[0].cell_id].population
    with pytest.raises(ValueError, match="SNAPSHOT_MISMATCH"):
        attach_demo_result(result, snapshot.model_copy(update={"snapshot_id": "real-city"}))


def test_map_cell_metadata_deduplicates_only_represented_population(
    transport_facts, demo, monkeypatch
):
    # Included-cell population already belongs to the exact sampled point feature.
    test_actual_product_output_keeps_model_labels_map_and_source_population(
        transport_facts, demo, monkeypatch
    )
    snapshot = demo[0]
    result = product.calculate(claim_for(snapshot, request_for(snapshot)))
    represented = {point.cell_id for point in snapshot.sample_points}
    retained = [feature for feature in result.map_data.features if feature.entity_kind == "cell"]
    expected = {
        cell.cell_id: cell for cell in snapshot.population_cells if cell.cell_id not in represented
    }
    assert {feature.cell_id for feature in retained} == {
        cell.cell_id for cell in snapshot.population_cells
    }
    assert all(
        feature.population == expected[feature.cell_id].population
        for feature in retained
        if feature.cell_id in expected
    )
    assert all(feature.population is None for feature in retained if feature.cell_id in represented)
