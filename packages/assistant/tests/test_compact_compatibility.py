"""Synthetic saved-result compatibility with PR #41; no runtime or LIVE calls."""

import json
from pathlib import Path

import pytest
from a2_assistant.core import render_explanation
from a2_contracts.v2.results import AnalysisResult
from test_assistant import analysis, score_analysis
from test_freeform import answer


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["urban_passenger", "intercity_passenger", "urban_cargo", "intercity_cargo"]
)
async def test_existing_four_kind_results_keep_saved_claims_and_request(kind):
    path = (
        Path(__file__).resolve().parents[3]
        / "packages/contracts/fixtures/v2"
        / f"{kind}-result.json"
    )
    saved = AnalysisResult.model_validate(json.loads(path.read_text())["payload"])
    before = saved.model_dump_json()
    metric = saved.metrics[0]
    result, transport, *_ = await answer(
        saved,
        "Объясни сохранённое время",
        "explain",
        findings=[{"metric_id": metric.metric_id, "explanation_code": "saved_value"}],
    )
    assert result.status == "generated"
    claim = result.findings[0].claims[0]
    assert (claim.metric_id, claim.value, claim.unit) == (
        metric.metric_id,
        metric.value,
        metric.unit,
    )
    facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    assert facts["request"]["analysis_kind"] == kind
    assert set(result.findings[0].evidence_refs) <= {e.evidence_id for e in saved.evidence_refs}
    assert render_explanation(result, saved)
    assert saved.model_dump_json() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_tau", [False, True])
@pytest.mark.parametrize("value", [25.0, None])
async def test_bounded_and_legacy_unknown_partial_keep_limits_without_invented_counts(
    legacy_tau, value
):
    raw = score_analysis(value=value, partial=True).model_dump(mode="json")
    raw["limitations"] = ["BOUNDED_URBAN_SAMPLE"]
    if legacy_tau:
        metric = analysis().metrics[0].model_dump(mode="json")
        metric.update(
            metric_id="legacy-count",
            name="accessible_object_count",
            value=None,
            unit="objects",
            status="unknown",
            reason_code="TAU_UNAVAILABLE",
            completeness="partial",
        )
        raw["metrics"] = [metric]
        raw["counts"] = [
            {
                "metric_id": "legacy-count",
                "threshold_seconds": None,
                "value": None,
                "status": "unknown",
                "reason_code": "TAU_UNAVAILABLE",
                "dependency_job_id": None,
                "evidence_refs": ["evidence"],
            }
        ]
    saved = AnalysisResult.model_validate(raw)
    before = saved.model_dump_json()
    code = "unknown" if value is None else "saved_value"
    result, transport, *_ = await answer(
        saved,
        "Какие ограничения у оценки?",
        "coverage",
        findings=[{"score_id": "accessibility", "explanation_code": code}],
    )
    assert result.status == "generated"
    assert "BOUNDED_URBAN_SAMPLE" in result.limitations
    assert result.findings[0].score_claims[0].value == value
    assert not result.findings[0].claims
    assert result.findings[0].evidence_refs == ["evidence"]
    facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    assert facts["scores"][0]["completeness"] == "partial"
    assert facts["scores"][0]["status"] == ("unknown" if value is None else "available")
    assert "BOUNDED_URBAN_SAMPLE" in facts["limitations"]
    assert "τ" not in render_explanation(result, saved)
    assert saved.model_dump_json() == before
