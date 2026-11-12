"""Deterministic selection of saved relationships; offline model only."""

import json

import pytest
from a2_assistant.core import _fact
from a2_assistant.focus import context_facts
from a2_contracts.v2.assistant import ExplanationRequest
from a2_contracts.v2.evidence import require_result_evidence
from a2_contracts.v2.results import AnalysisResult
from test_assistant import analysis, score_analysis
from test_freeform import answer


def linked_result(linked=True):
    raw = score_analysis(partial=True).model_dump(mode="json")
    metric = analysis().metrics[0].model_dump(mode="json")
    raw["metrics"] = [metric | {"metric_id": f"unrelated-{i}"} for i in range(30)]
    raw["metrics"].append(metric | {"metric_id": "late-component"})
    raw["scores"][0]["components"] = [{"kind": "metric", "id": "late-component"}] if linked else []
    return AnalysisResult.model_validate(raw)


def test_why_follows_only_real_edges_beyond_twenty():
    request = ExplanationRequest(question="Почему такой индекс?")
    metrics, scores = context_facts(linked_result(), request)
    assert metrics[0].metric_id == "late-component"
    assert scores[0].score_id == "accessibility"
    assert "late-component" not in {
        m.metric_id for m in context_facts(linked_result(False), request)[0]
    }


def test_compare_keeps_selection_and_explicit_second_saved_subject():
    raw = score_analysis().model_dump(mode="json")
    raw["scores"].append(
        raw["scores"][0]
        | {"score_id": "second", "subject": {"kind": "sample_point", "point_id": "other-point"}}
    )
    request = ExplanationRequest(
        question="Сравни point и other-point",
        selection={"entity_kind": "sample_point", "entity_id": "point"},
    )
    _, scores = context_facts(AnalysisResult.model_validate(raw), request)
    assert [s.score_id for s in scores] == ["accessibility", "second"]


@pytest.mark.asyncio
async def test_context_preserves_edges_sources_limits_and_opaque_values():
    saved = linked_result()
    saved.limitations = ["BOUNDED_URBAN_SAMPLE"]
    result, transport, *_ = await answer(saved, "Почему такой индекс? Источник?", "coverage")
    assert result.status == "generated"
    payload = json.loads(transport.calls[0]["messages"][1]["content"])
    facts = payload["saved_facts"]
    assert payload["facts_truncated"]
    assert facts["methodology_version"] == saved.methodology_version
    assert facts["limitations"] == saved.limitations
    assert facts["counts_state"] == "not_in_saved_result"
    assert facts["scores"][0]["components"] == [{"kind": "metric", "id": "late-component"}]
    assert facts["metrics"][0]["metric_id"] == "late-component"
    assert "placeholder" not in facts["metrics"][0]
    assert "value" not in facts["metrics"][0]
    assert result.findings[0].score_claims[0].value == 25.0
    if "sources" in facts:
        ref = saved.evidence_refs[0]
        assert facts["sources"][0]["url"] == str(ref.url)
        assert facts["sources"][0]["retrieved_at"] == ref.retrieved_at.isoformat()
    call = transport.calls[0]
    encoded = json.dumps(
        {"messages": call["messages"], "tools": call["tools"]},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    assert len(encoded) + 1024 <= 12000


def test_movement_is_never_promoted_to_complete_delivery():
    saved = analysis()
    metric = saved.metrics[0].model_copy(update={"time_kind": "movement"})
    fact = _fact(metric, require_result_evidence(saved))
    assert fact["time_kind"] == "movement"
    assert fact["metric_id"] == "travel-time" and "placeholder" not in fact


@pytest.mark.asyncio
async def test_saved_journey_summary_keeps_its_time_kind_and_leg_modes():
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[3]
        / "packages/contracts/fixtures/v2/intercity_passenger-result.json"
    )
    saved = AnalysisResult.model_validate(json.loads(path.read_text())["payload"])
    metric = saved.metrics[0]
    result, transport, *_ = await answer(
        saved,
        "Какой участок маршрута занимает время?",
        "journey",
        findings=[{"metric_id": metric.metric_id, "explanation_code": "travel_time"}],
    )
    assert result.status == "generated"
    facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    journey = next(j for j in saved.journeys if j.journey_id == metric.journey_id)
    summary = facts["journeys"][0]
    assert summary["time_kind"] == journey.time_kind
    assert summary["legs"] == [{"kind": leg.kind, "mode": leg.mode} for leg in journey.legs[:4]]
    assert "total_seconds" not in summary


@pytest.mark.asyncio
async def test_source_question_contains_only_saved_provenance():
    saved = score_analysis(partial=True)
    result, transport, *_ = await answer(saved, "Какой источник у оценки?", "coverage")
    assert result.status == "generated"
    facts = json.loads(transport.calls[0]["messages"][1]["content"])["saved_facts"]
    source = facts["sources"][0]
    ref = saved.evidence_refs[0]
    assert source["evidence_id"] == ref.evidence_id
    assert source["url"] == str(ref.url)
    assert source["retrieved_at"] == ref.retrieved_at.isoformat()
    assert "acceptance_state" not in source and "valid_period" not in source


@pytest.mark.asyncio
async def test_oversized_mandatory_limits_fall_back_without_model_call():
    saved = score_analysis(partial=True)
    saved.limitations = [f"BOUND_{i}_" + "ж" * 900 for i in range(20)]
    result, transport, *_ = await answer(saved, "Почему такой индекс?", "coverage")
    assert result.status == "template"
    assert result.reason_code == "INPUT_LIMIT"
    assert not transport.calls
