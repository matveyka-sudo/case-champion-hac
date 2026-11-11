"""TEST_ONLY: a bounded overview must not conceal all late numerical facts."""

from a2_assistant.core import template_explanation
from a2_assistant.focus import focused_scores
from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.results import AnalysisResult
from test_assistant import analysis, score_analysis


def large_partial_result(value=0.4971801994143471):
    payload = score_analysis(value=None, partial=True).model_dump(mode="json")
    unknown = payload["scores"][0]
    known = score_analysis(value=value, partial=True).model_dump(mode="json")["scores"][0]
    payload["scores"] = [
        unknown
        | {
            "score_id": f"unknown-{i}",
            "subject": {"kind": "sample_point", "point_id": f"point-{i}"},
        }
        for i in range(100)
    ] + [
        known | {"score_id": "late-known"},
        unknown
        | {"score_id": "city-total", "subject": {"kind": "territory", "territory_id": "city"}},
    ]
    payload["metrics"] = analysis().model_dump(mode="json")["metrics"]
    return AnalysisResult.model_validate(payload)


def test_overview_keeps_known_point_and_unknown_total_without_mutating_result():
    saved = large_partial_result()
    before = saved.model_dump_json()
    answer = template_explanation(saved, "request", "DISABLED")
    claims = {
        claim.score_id: claim.value for finding in answer.findings for claim in finding.score_claims
    }
    assert claims["late-known"] == 0.4971801994143471
    assert claims["city-total"] is None
    assert any(key.startswith("unknown-") for key in claims)
    assert any(finding.claims for finding in answer.findings)
    assert len(answer.findings) <= 20
    assert answer.status == "template" and answer.model_id is None
    assert saved.model_dump_json() == before
    assert any("остальные доступны" in text for text in answer.limitations)


def test_overview_preserves_zero_as_known_and_respects_explicit_selection():
    saved = large_partial_result(0.0)
    assert any(s.score_id == "late-known" and s.value == 0 for s in focused_scores(saved))
    context = ExplanationRequest(selection={"entity_kind": "sample_point", "entity_id": "point-99"})
    answer = template_explanation(saved, "request", "DISABLED", context=context)
    assert [claim.score_id for f in answer.findings for claim in f.score_claims] == ["unknown-99"]
    assert answer.findings[0].score_claims[0].value is None
    context = ExplanationRequest(selection={"entity_kind": "sample_point", "entity_id": "missing"})
    assert template_explanation(saved, "request", "DISABLED", context=context).findings == []


def test_overview_is_bounded_and_deterministic():
    saved = large_partial_result()
    first = focused_scores(saved)
    assert len(first) == 20
    assert first == focused_scores(saved)
    assert len({s.score_id for s in first}) == 20
