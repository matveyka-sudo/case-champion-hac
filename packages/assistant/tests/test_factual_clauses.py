"""Factual v2 uses closed server clauses, not model-authored transport claims."""

import pytest
from a2_assistant.core import render_explanation
from test_assistant import reply, score_analysis
from test_context_v2 import linked_result
from test_freeform import answer
from test_grounded_prose import prose_reply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claim",
    [
        "Дорожное покрытие разрушено.",
        "Плохое полотно замедляет грузовики.",
        "Время пути включает ожидание отправления.",
        "Индекс учитывает спрос.",
        "Морское сообщение доступно.",
        "Международный транзит запрещён.",
        "Сценарий обеспечит улучшение.",
        "Покрытие полное.",
    ],
)
async def test_real_reference_cannot_authorize_a_fabricated_clause(claim):
    result, *_ = await answer(
        score_analysis(partial=True),
        "Почему?",
        "explain",
        responses=[prose_reply("Сохранено [[score:accessibility]]. " + claim)],
    )
    assert result.status == "template"
    assert result.reason_code == "INVALID_EXPLANATION"


@pytest.mark.asyncio
@pytest.mark.parametrize("linked", [True, False])
async def test_component_clause_requires_the_actual_selected_edge(linked):
    saved = linked_result(linked)
    metric = "late-component" if linked else "unrelated-0"
    result, *_ = await answer(
        saved,
        "Почему такой индекс?",
        "explain",
        responses=[
            reply(
                {
                    "clause_id": "components",
                    "findings": [
                        {"score_id": "accessibility", "explanation_code": "saved_value"},
                        {"metric_id": metric, "explanation_code": "saved_value"},
                    ],
                }
            )
        ],
    )
    assert (result.status == "generated") is linked
    if linked:
        assert "компонентами" in render_explanation(result, saved)
        assert result.findings[0].claims[0].value == saved.metrics[-1].value


@pytest.mark.asyncio
async def test_unknown_partial_and_limitations_survive_server_clause():
    saved = score_analysis(value=None, partial=True)
    saved.limitations = ["FOREIGN_TRANSIT_DATA_UNAVAILABLE"]
    result, *_ = await answer(
        saved,
        "Транзит запрещён?",
        "coverage",
        responses=[
            reply(
                {
                    "clause_id": "coverage",
                    "findings": [{"score_id": "accessibility", "explanation_code": "unknown"}],
                }
            )
        ],
    )
    assert result.status == "generated"
    text = render_explanation(result, saved)
    assert "Неизвестное значение не означает ноль" in text
    assert "запрещён" not in text and "FOREIGN_TRANSIT_DATA_UNAVAILABLE" in result.limitations
    assert result.findings[0].score_claims[0].value is None
    assert result.findings[0].evidence_refs == ["evidence"]


def test_existing_import_facades_and_transport_remain_available():
    from a2_assistant import FlashAssistant, FlashConfig, SDKTransport
    from a2_assistant.clauses import _ModelSelection
    from a2_assistant.core import _ModelSelection as CompatSelection
    from a2_assistant.tools import tool_definitions

    assert CompatSelection is _ModelSelection
    assert callable(FlashAssistant) and callable(FlashConfig) and callable(SDKTransport)
    assert len(tool_definitions()) == 8


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["movement", "complete_chain"])
async def test_time_scope_uses_saved_time_kind_only(kind):
    from test_assistant import analysis

    saved = analysis()
    saved.metrics[0].time_kind = kind
    result, *_ = await answer(
        saved,
        "Это время доставки?",
        "explain",
        findings=[{"metric_id": "travel-time", "explanation_code": "saved_value"}],
        responses=[
            reply(
                {
                    "clause_id": "time_scope",
                    "findings": [{"metric_id": "travel-time", "explanation_code": "saved_value"}],
                }
            )
        ],
    )
    assert result.status == "generated"
    text = render_explanation(result, saved)
    assert ("не подтверждает полное время грузовой доставки" in text) is (kind == "movement")
    assert result.findings[0].claims[0].value == 1800
