import pytest
from a2_contracts.v2.results import AnalysisResult, Metric, Score
from pydantic import ValidationError
from test_v2_requests import cases


def score():
    return dict(
        score_id="demo-score",
        name="Synthetic category index",
        value=50,
        status="available",
        completeness="full",
        methodology_version="a2-methodology-v3",
        reason_code=None,
        components=[{"kind": "metric", "id": "demo-time"}],
        evidence_refs=["demo-evidence"],
        subject={
            "kind": "sample_point",
            "point_id": "demo-point",
            "category": "school",
            "mode": "walk",
        },
        ranking_eligible=False,
    )


def metric():
    return dict(
        metric_id="demo-time",
        name="Synthetic elapsed travel time",
        value=1800,
        unit="seconds",
        status="available",
        reason_code=None,
        source_refs=["demo-evidence"],
        subject={
            "kind": "sample_point",
            "point_id": "demo-point",
            "category": "school",
            "mode": "walk",
        },
        population_basis=None,
        site_basis=None,
        completeness="full",
        dependency_job_id=None,
    )


def analysis():
    request = cases()[0] | {
        "snapshot_id": "demo-snapshot",
        "methodology": dict(
            version="a2-methodology-v3",
            config_hash="demo-config",
            reference_profile="edge-calibrated-v1",
        ),
    }
    return dict(
        schema_version="2",
        analysis_id="demo-analysis",
        analysis_kind="urban_passenger",
        request=request,
        snapshot_id="demo-snapshot",
        methodology_version="a2-methodology-v3",
        config_hash="demo-config",
        traffic_mode="static",
        status="available",
        metrics=[metric()],
        scores=[score()],
        evidence_refs=[
            dict(
                evidence_id="demo-evidence",
                source_id="demo-source",
                url="https://example.org/synthetic",
                retrieved_at="2026-11-08T09:00:00Z",
                observed_at=None,
                section="Synthetic test only",
            )
        ],
        limitations=["synthetic_only"],
        versions=dict(
            snapshot_id="demo-snapshot",
            graph_version="demo-graph",
            calendar_version="demo-calendar",
            population_version=None,
            methodology_version="a2-methodology-v3",
            config_hash="demo-config",
            code_version="demo-code",
        ),
        journeys=[],
        references=[],
        counts=[],
        last_returns=[],
        result_hash="demo-hash",
    )


@pytest.mark.parametrize("status", ["unknown", "not_applicable", "methodology_pending"])
def test_score_status_never_turns_missing_values_into_zero(status):
    with pytest.raises(ValidationError):
        Score.model_validate(
            score() | {"status": status, "value": 0, "reason_code": "DATA_UNAVAILABLE"}
        )
    assert (
        Score.model_validate(
            score() | {"status": status, "value": None, "reason_code": "DATA_UNAVAILABLE"}
        ).value
        is None
    )


def test_confirmed_no_route_is_available_zero_with_evidence():
    value = Score.model_validate(score() | {"value": 0, "reason_code": "NO_FEASIBLE_ROUTE"})
    assert value.value == 0
    with pytest.raises(ValidationError):
        Score.model_validate(
            score() | {"value": 0, "reason_code": "NO_FEASIBLE_ROUTE", "completeness": "partial"}
        )


def test_partial_aggregate_cannot_enter_full_ranking():
    with pytest.raises(ValidationError):
        Score.model_validate(score() | {"completeness": "partial", "ranking_eligible": True})
    with pytest.raises(ValidationError):
        Score.model_validate(score() | {"value": float("nan")})


def test_metric_pending_is_separate_from_approved_h_score():
    data = analysis()
    data["metrics"].append(
        metric()
        | {
            "metric_id": "demo-count",
            "value": None,
            "unit": "objects",
            "status": "pending",
            "reason_code": "TAU_PENDING",
            "dependency_job_id": "demo-tau-job",
        }
    )
    data["status"] = "partial"
    result = AnalysisResult.model_validate(data)
    assert result.scores[0].value == 50
    assert result.metrics[1].value is None


@pytest.mark.parametrize(
    "change", ["unknown_component", "unknown_evidence", "duplicate_metric", "version"]
)
def test_saved_results_bind_versions_and_all_references(change):
    data = analysis()
    if change == "unknown_component":
        data["scores"][0]["components"][0]["id"] = "missing"
    elif change == "unknown_evidence":
        data["metrics"][0]["source_refs"] = ["missing"]
    elif change == "duplicate_metric":
        data["metrics"].append(metric())
    else:
        data["versions"]["snapshot_id"] = "other-snapshot"
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_valid_result_carries_normalized_request_and_finite_values():
    result = AnalysisResult.model_validate(analysis())
    assert result.request.analysis_kind == result.analysis_kind
    with pytest.raises(ValidationError):
        Metric.model_validate(metric() | {"value": float("inf")})
