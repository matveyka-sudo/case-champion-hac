"""TEST_ONLY: public-engine controls projected through real canonical validators."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from a2_api.adapters.product_projection import (
    Projection,
    project_delivery,
    project_intercity,
    project_pipeline,
    project_urban,
    project_urban_cargo,
)
from a2_api.core.jobs import V2JobContext
from a2_api.core.registry import ProductRegistry
from a2_contracts.v2.base import VersionRefs
from a2_contracts.v2.requests import parse_analysis_request
from a2_contracts.v2.results import AnalysisResult, EvidenceRef
from a2_engine import build_reference, create_scenario_baseline

ROOT = Path(__file__).resolve().parents[3]


def example(name):
    spec = importlib.util.spec_from_file_location(
        "TEST_ONLY_" + name, ROOT / "packages/engine/examples" / ("synthetic_" + name + ".py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def projection(kind, proof, **request_fields):
    config = ProductRegistry()
    request = parse_analysis_request(
        dict(
            schema_version="2",
            analysis_kind=kind,
            traffic_mode="static",
            departure_at="2026-11-09T00:00:00Z",
            snapshot_id="snapshot",
            methodology=config.methodology,
            **request_fields,
        )
    )
    context = V2JobContext(
        schema_version="2",
        snapshot_id="snapshot",
        snapshot_hash="TEST_ONLY-snapshot",
        versions=VersionRefs(
            snapshot_id="snapshot",
            graph_version="graph",
            calendar_version="calendar",
            population_version="population",
            methodology_version=config.methodology.version,
            config_hash=config.config_hash,
            code_version="TEST_ONLY",
        ),
        analysis_id="TEST_ONLY-analysis",
        normalized_request=request,
        access_scope="TEST_ONLY-scope",
    )
    evidence = [
        EvidenceRef(
            evidence_id=identity,
            source_id="TEST_ONLY-source",
            url="https://example.test/TEST_ONLY",
            retrieved_at=datetime(2026, 11, 9, tzinfo=UTC),
            observed_at=None,
            section="TEST_ONLY",
        )
        for identity in proof
    ]
    return Projection(context, evidence)


@pytest.mark.parametrize("kind", ["urban", "intercity", "urban_cargo", "delivery", "pipeline"])
def test_public_engine_results_project_to_valid_canonical_dto(kind):
    module = example("scenario" if kind == "urban" else kind)
    if kind == "urban":
        inputs, _, _ = module.demo_inputs()
        baseline = create_scenario_baseline(inputs)
        result = baseline.before.urban[0].analysis
        out = projection(
            "urban_passenger",
            module.PROOF,
            city_id=result.context.city_id,
            passenger_mode="public_transport",
            categories=list(result.selected_categories),
            priorities=dict(result.selected_categories),
        )
        out.context.normalized_request.departure_at = result.context.requested_departure_at
        project_urban(out, result)
    elif kind == "intercity":
        result = module.run_demo()
        out = projection(
            "intercity_passenger",
            module.PROOF,
            territory_level="city",
            origin_id="city-A",
            destination_id="city-B",
        )
        project_intercity(
            out,
            result,
            build_reference(module.demo_reference(result.scope), result.scope.comparison_scope),
        )
    elif kind == "urban_cargo":
        result = module.run_demo()
        out = projection(
            "urban_cargo",
            module.PROOF,
            city_id="city",
            cargo_profile_id=module.PROFILE.profile_id,
            direction="outbound",
        )
        project_urban_cargo(out, result)
    elif kind == "delivery":
        result = module.run_demo()
        out = projection(
            "intercity_cargo",
            module.PROOF,
            territory_level="city",
            origin_id=result.scope.origin_territory_id,
            destination_id=result.scope.destination_territory_id,
            cargo_profile_id=module.PROFILE.profile_id,
            cargo_transport="multimodal",
        )
        project_delivery(out, result)
    else:
        result = module.run_demo()
        out = projection(
            "intercity_cargo",
            module.PROOF,
            territory_level="city",
            origin_id=result.scope.origin_territory_id,
            destination_id=result.scope.destination_territory_id,
            cargo_profile_id="TEST_ONLY-oil",
            cargo_transport="pipeline",
            pipeline_product="oil",
        )
        project_pipeline(out, result)
    saved = out.finish()
    assert AnalysisResult.model_validate_json(saved.model_dump_json()) == saved
    assert any(item.value is not None for item in saved.metrics)
    if kind == "pipeline":
        assert not saved.scores
        assert saved.metrics[0].value == 120
    else:
        assert any(item.value is not None for item in saved.scores)
    if kind == "intercity":
        total = next(
            metric for metric in saved.metrics if metric.metric_id == "intercity-public-total"
        )
        assert total.value == result.public.best_known.total_seconds == 5400
        assert total.time_kind == "complete_chain" and total.journey_id is not None
    if kind == "urban":
        absent = [metric for metric in saved.metrics if metric.reason_code == "NO_FEASIBLE_ROUTE"]
        assert absent and all(
            metric.value is None
            and metric.status == "not_applicable"
            and metric.completeness == "full"
            for metric in absent
        )
