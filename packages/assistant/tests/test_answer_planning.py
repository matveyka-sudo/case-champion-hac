"""Offline synthetic golden questions: planning never grants factual authority."""

import json
from copy import deepcopy

import pytest
from a2_assistant.context.focus import answer_intent, planned_facts
from a2_assistant.core import _ModelAnswerRejected, _parse_model_content, render_explanation
from a2_contracts.v2.results import AnalysisResult
from test_assistant import analysis, reply, score_analysis
from test_context_v2 import linked_result
from test_freeform import answer


@pytest.mark.parametrize(
    "question,intent",
    [
        ("Почему у точек разные результаты?", "explain_difference"),
        ("Почему этот индекс ниже?", "explain_difference"),
        ("Почему значение выше?", "explain_difference"),
        ("Сравни точки", "explain_difference"),
        ("Объясни различия", "explain_difference"),
        ("Почему такой индекс?", "explain_score"),
        ("Объясни оценку", "explain_score"),
        ("Что означает результат?", "explain_score"),
        ("Как улучшить транспортную доступность Орехово-Борисова?", "improvement_hypotheses"),
        ("Какие изменения могут улучшить индекс?", "improvement_hypotheses"),
        ("Как ускорить поездку?", "improvement_hypotheses"),
        ("Что проверить дальше?", "improvement_hypotheses"),
        ("Что можешь рассказать об этом районе?", "area_overview"),
        ("Дай обзор", "area_overview"),
        ("Что известно о территории?", "area_overview"),
        ("На чём основаны данные?", "sources"),
        ("Откуда значения?", "sources"),
        ("Какие источники?", "sources"),
        ("Что означает partial coverage?", "clarify_unknown"),
        ("Почему нет данных?", "clarify_unknown"),
        ("Что означает unknown?", "clarify_unknown"),
    ],
)
@pytest.mark.asyncio
async def test_golden_question_intent(question, intent):
    assert answer_intent(question) == intent
    saved = score_analysis(partial=True)
    saved.limitations = ["SYNTHETIC_TEST_ONLY"]
    _, transport, *_ = await answer(saved, question, "explain")
    example = json.loads(transport.calls[0]["messages"][1]["content"])["response_example"]
    result, transport, *_ = await answer(saved, question, "explain", responses=[reply(example)])
    assert result.status == "generated"
    assert result.limitations[0] == "SYNTHETIC_TEST_ONLY"
    assert result.findings[0].score_claims[0].value == saved.scores[0].value
    assert result.findings[0].evidence_refs == ["evidence"]
    assert len(transport.calls) == 1
    assert render_explanation(result, saved)


def test_plan_uses_actual_edges_and_at_most_three_atoms():
    saved = linked_result(True)
    metrics, scores = planned_facts(saved.metrics, saved.scores, "Почему такой индекс?")
    assert [m.metric_id for m in metrics] == ["late-component"]
    assert [s.score_id for s in scores] == ["accessibility"]
    saved = linked_result(False)
    assert planned_facts(saved.metrics, saved.scores, "Почему такой индекс?")[0] == []


def test_difference_plan_requires_same_scope_and_semantics():
    raw = score_analysis().model_dump(mode="json")
    other = deepcopy(raw["scores"][0])
    other.update(score_id="second", value=75.0)
    other["subject"]["point_id"] = "second-point"
    raw["scores"].append(other)
    saved = AnalysisResult.model_validate(raw)
    _, scores = planned_facts([], saved.scores, "Почему у точек разные результаты?")
    assert [s.score_id for s in scores] == ["accessibility", "second"]
    saved.scores[1].subject.mode = "private_car"
    assert len(planned_facts([], saved.scores, "Почему у точек разные результаты?")[1]) == 1


@pytest.mark.parametrize(
    "extra",
    [
        {"template_ids": []},
        {"evidence_ids": []},
        {"clause_id": "explain"},
    ],
)
def test_bug001_nested_contract_fields_remain_forbidden(extra):
    with pytest.raises(_ModelAnswerRejected) as exc:
        _parse_model_content(
            json.dumps(
                {
                    "clause_id": "explain",
                    "findings": [{"score_id": "accessibility", **extra}],
                }
            )
        )
    assert exc.value.category == "schema_invalid"


@pytest.mark.asyncio
async def test_recommended_atoms_generate_with_exact_saved_values_and_limits():
    saved = linked_result(True)
    saved.limitations = ["SYNTHETIC_TEST_ONLY", "INCOMPLETE_INPUT"]
    _, transport, *_ = await answer(saved, "Почему такой индекс?", "explain")
    payload = json.loads(transport.calls[0]["messages"][1]["content"])
    example = payload["response_example"]
    assert example["clause_id"] == "explain"
    result, *_ = await answer(saved, "Почему такой индекс?", "explain", responses=[reply(example)])
    assert result.status == "generated"
    assert result.limitations[:2] == saved.limitations
    assert result.findings[0].claims[0].value == saved.metrics[-1].value
    assert render_explanation(result, saved)


@pytest.mark.asyncio
async def test_valid_schema_does_not_authorize_foreign_reference():
    result, *_ = await answer(
        score_analysis(),
        "Почему?",
        "explain",
        responses=[
            reply(
                {
                    "clause_id": "explain",
                    "findings": [{"score_id": "foreign"}],
                    "evidence_ids": [],
                    "template_ids": [],
                }
            )
        ],
    )
    assert result.status == "template"
    assert result.reason_code == "INVALID_EXPLANATION"


@pytest.mark.parametrize("time_kind", ["movement", "complete_chain"])
def test_time_comparison_keeps_time_semantics(time_kind):
    saved = analysis()
    first = saved.metrics[0]
    first.subject.point_id = "first"
    first.subject.kind = "sample_point"
    first.time_kind = "movement"
    second = first.model_copy(deep=True)
    second.metric_id = "second"
    second.subject.point_id = "second"
    second.time_kind = time_kind
    metrics, _ = planned_facts([first, second], [], "Почему у точек разные результаты?")
    assert len(metrics) == (2 if time_kind == "movement" else 1)


@pytest.mark.asyncio
async def test_improvement_is_a_question_to_verify_not_promised_effect():
    saved = score_analysis(partial=True)
    result, *_ = await answer(saved, "Как улучшить доступность района?", "improve")
    assert result.status == "generated"
    text = result.findings[0].text
    assert "Гипотеза для проверки:" in text
    assert "не обещание эффекта" in text
    assert "Сначала проверьте" in text
    assert result.proposals == []


@pytest.mark.asyncio
async def test_area_overview_does_not_invent_local_objects():
    saved = score_analysis(partial=True)
    result, *_ = await answer(
        saved,
        "Что можешь рассказать об этом районе?",
        "explain",
        responses=[
            reply({"clause_id": "area_overview", "findings": [{"score_id": "accessibility"}]})
        ],
    )
    assert result.status == "generated"
    assert "по выбранной области" in result.findings[0].text
    assert "не описывают все объекты района" in result.findings[0].text
    assert result.findings[0].score_claims[0].value == saved.scores[0].value
