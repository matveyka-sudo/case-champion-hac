import pytest
from a2_contracts.v2.api import JobView
from a2_contracts.v2.results import AnalysisResult, Score, SubjectRef
from pydantic import ValidationError
from test_v2_results import analysis, score


def test_full_score_cannot_be_supported_by_unknown_operand():
    data = analysis()
    data["status"] = "partial"
    data["metrics"][0].update(status="unknown", value=None, reason_code="DATA_UNAVAILABLE")
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_score_component_graph_cannot_be_circular():
    data = analysis()
    data["scores"] = [
        score() | {"score_id": "a", "components": [{"kind": "score", "id": "b"}]},
        score() | {"score_id": "b", "components": [{"kind": "score", "id": "a"}]},
    ]
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_territorial_ranking_requires_known_positive_denominator():
    data = score() | {
        "ranking_eligible": True,
        "subject": {"kind": "territory", "territory_id": "demo-city"},
    }
    for basis in (None, {"site_count": 0, "weighting": "equal_physical_sites", "coverage": 1}):
        with pytest.raises(ValidationError):
            Score.model_validate(data | {"aggregation_basis": basis})


def test_subject_is_identifiable_and_count_fk_is_not_orphaned():
    with pytest.raises(ValidationError):
        SubjectRef(kind="sample_point")
    data = analysis()
    data["counts"] = [
        dict(
            metric_id="missing",
            threshold_seconds=1800,
            value=1,
            status="available",
            reason_code=None,
            dependency_job_id=None,
            evidence_refs=["demo-evidence"],
        )
    ]
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(data)


def test_public_job_preserves_terminal_state_invariants():
    with pytest.raises(ValidationError):
        JobView(
            job_id="demo",
            status="succeeded",
            request_id="demo",
            created_at="2026-11-08T09:00:00Z",
            started_at=None,
            finished_at=None,
            analysis_id=None,
            scenario_run_id=None,
            error=None,
        )
