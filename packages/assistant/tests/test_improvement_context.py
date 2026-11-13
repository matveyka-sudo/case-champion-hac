"""Recommendations must describe the saved grounds, never assume a transfer."""

import json
from pathlib import Path

import pytest
from a2_assistant.grounding.narrative import conversational_finding
from a2_contracts.v2.results import AnalysisResult
from test_assistant import score_analysis
from test_freeform import answer
from test_question_set import ask


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["walk", "public_transport", "private_car"])
async def test_no_saved_scenario_does_not_suggest_a_transfer(mode):
    saved = score_analysis(partial=True)
    saved.request.passenger_mode = mode
    _, result, text, _ = await ask(saved, "Как можно улучшить индекс?")
    assert result.status == "generated"
    assert "нет разрешённого сценария" in text
    assert "подтверждённая цепочка" in text
    assert "пересад" not in text and "по кнопке" not in text
    assert result.proposals == []
    assert result.findings[0].score_claims[0].value == saved.scores[0].value


def test_saved_scenario_is_separate_from_a_confirmed_effect():
    saved = score_analysis(partial=True)
    finding = conversational_finding(
        "improve", saved, [], [saved.scores[0].score_id], has_proposal=True
    )
    assert "разрешённый сохранённый сценарий" in finding.text
    assert "Эффект" in finding.text and "отдельно" in finding.text
    assert "пересад" not in finding.text


@pytest.mark.asyncio
async def test_synthetic_result_is_disclosed_inside_the_answer():
    saved = score_analysis(partial=True)
    saved.limitations = ["synthetic_only"]
    result, *_ = await answer(saved, "Почему такая оценка?", "explain")
    text = result.findings[0].text
    assert "синтетическая" in text and "не рейтинг реального города" in text


@pytest.mark.parametrize("select_journey_metric", [False, True])
def test_only_a_selected_available_journey_supports_breaking_down_the_path(select_journey_metric):
    path = Path(__file__).resolve().parents[3] / (
        "packages/contracts/fixtures/v2/intercity_passenger-result.json"
    )
    saved = AnalysisResult.model_validate(json.loads(path.read_text())["payload"])
    metric = saved.metrics[0]
    finding = conversational_finding(
        "improve",
        saved,
        [metric.metric_id] if select_journey_metric else [],
        [] if select_journey_metric else [saved.scores[0].score_id],
        has_proposal=False,
    )
    assert ("Можно разобрать длительность участков" in finding.text) is select_journey_metric
    assert "пересад" not in finding.text
