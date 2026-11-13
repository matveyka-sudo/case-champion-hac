"""One offline model answer can vary its prose without supplying numeric facts."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from a2_assistant.core import render_explanation
from a2_contracts.v2.assistant import Explanation, ExplanationRequest
from a2_contracts.v2.results import AnalysisResult
from pydantic import HttpUrl
from test_assistant import Backend, assistant, reply, scope, score_analysis, tool
from test_freeform import answer


def only_document_search(calls):
    """Server-side routing may run one bounded document search; no other tool."""
    return [call[0] for call in calls] in ([], ["search_evidence"])


def prose_reply(paragraph, *, findings=None, evidence_ids=None, **extra):
    return reply(
        {
            "paragraph": paragraph,
            "findings": findings
            or [{"score_id": "accessibility", "explanation_code": "saved_value"}],
            "evidence_ids": ["evidence"] if evidence_ids is None else evidence_ids,
            **extra,
        }
    )


@pytest.mark.asyncio
async def test_question_specific_prose_is_not_replaced_by_an_intent_paragraph():
    saved = score_analysis(partial=True)
    result, transport, backend, budget = await answer(
        saved,
        "Если индекс слабый, это обязательно плохие дороги или у результата есть другие ограничения?",
        "explain",
        responses=[
            reply(
                {
                    "paragraph": "Здесь важно отделить численную оценку от объяснения её причины. "
                    "Для выбранной точки сохранено [[score:accessibility]]. По этому итогу нельзя "
                    "установить качество дорог: для такого вывода нужны данные о самой цепочке.",
                    "findings": [{"score_id": "accessibility", "explanation_code": "saved_value"}],
                    "evidence_ids": ["evidence"],
                }
            )
        ],
    )
    assert result.status == "generated"
    assert "отделить численную оценку" in result.findings[0].text
    assert "25" in result.findings[0].text
    assert "Полнота данных ограничена" in result.findings[0].text
    assert result.findings[0].score_claims[0].value == 25
    assert len(transport.calls) == len(budget.reservations) == 1
    assert only_document_search(backend.calls)
    assert render_explanation(result, saved)
    assert render_explanation(deepcopy(result), saved)
    assert Explanation.model_validate_json(result.model_dump_json()).findings == result.findings


@pytest.mark.asyncio
async def test_two_saved_points_get_question_specific_tradeoff_and_server_comparison():
    raw = score_analysis().model_dump(mode="json")
    raw["scores"].append(
        raw["scores"][0]
        | {
            "score_id": "other",
            "value": 75.0,
            "subject": {"kind": "sample_point", "point_id": "other-point"},
        }
    )
    saved = AnalysisResult.model_validate(raw)
    paragraph = (
        "Выбор места требует понять, что именно сравнивается: сохранены "
        "[[score:accessibility]] и [[score:other]]. [[comparison]] "
        "Качество дорог и стоимость переезда из этих записей установить нельзя."
    )
    result, transport, backend, budget = await answer(
        saved,
        "Сравни точки, но поможет ли этот индекс выбрать место для переезда?",
        "compare",
        responses=[
            prose_reply(
                paragraph,
                findings=[
                    {"score_id": s.score_id, "explanation_code": "saved_value"}
                    for s in saved.scores
                ],
            )
        ],
    )
    text = render_explanation(result, saved)
    assert result.status == "generated"
    assert "Выбор места требует понять" in text and "У второго" in text
    assert "25 из 100 и 75 из 100" in text and "50" not in text
    assert [claim.value for claim in result.findings[0].score_claims] == [25.0, 75.0]
    assert len(transport.calls) == len(budget.reservations) == 1
    assert only_document_search(backend.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("other_value", [None, 75.0])
@pytest.mark.parametrize("correct_hash", [False, True])
async def test_paragraph_second_point_requires_matching_scoped_saved_lookup(
    correct_hash, other_value
):
    raw = score_analysis(partial=True).model_dump(mode="json")
    other = score_analysis(value=other_value, partial=True).model_dump(mode="json")["scores"][0]
    raw["scores"].append(
        other
        | {"score_id": "other", "subject": {"kind": "sample_point", "point_id": "other-point"}}
    )
    saved = AnalysisResult.model_validate(raw)
    backend = Backend(
        {
            "analysis_id": saved.analysis_id,
            "result_hash": saved.result_hash if correct_hash else "wrong-hash",
            "metrics": [],
            # Tool numbers are deliberately false; they must never enter prose/claims.
            "scores": [{"score_id": "other", "value": 999}],
        }
    )
    lookup = {
        "analysis_id": saved.analysis_id,
        "selection": {"entity_kind": "sample_point", "entity_id": "other-point"},
    }
    agent, transport, _, budget = assistant(
        [
            reply(calls=[tool("get_analysis", lookup)], finish_reason="tool_calls"),
            prose_reply(
                "Сначала сопоставим сами записи: [[score:accessibility]] и [[score:other]]. "
                "[[comparison]]",
                findings=[
                    {"score_id": "accessibility", "explanation_code": "saved_value"},
                    {
                        "score_id": "other",
                        "explanation_code": "unknown" if other_value is None else "saved_value",
                    },
                ],
            ),
        ],
        backend=backend,
    )
    result = await agent.explain(
        saved,
        access_scope=scope(),
        registered_template_ids=set(),
        request_id="TEST_ONLY-lookup",
        context=ExplanationRequest(
            question="Сравни выбранную точку с другой, не подменяя неизвестное числом",
            selection={"entity_kind": "sample_point", "entity_id": "point"},
        ),
    )
    lookups = [call for call in backend.calls if call[0] != "search_evidence"]
    assert lookups[0][:2] == ("get_analysis", lookup)
    assert len(transport.calls) == len(budget.reservations) == 2
    if correct_hash:
        assert result.status == "generated"
        text = render_explanation(result, saved)
        assert "Сначала сопоставим сами записи" in text and "999" not in text
        assert [claim.value for claim in result.findings[0].score_claims] == [25.0, other_value]
        assert ("Сравнение неполное" in text) == (other_value is None)
        exposed = json.loads(transport.calls[1]["messages"][-1]["content"])
        assert exposed["untrusted_tool_data"]["scores"][0]["score_id"] == "other"
        assert "placeholder" not in exposed["untrusted_tool_data"]["scores"][0]
    else:
        assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"
        assert result.findings[0].score_ids == ["accessibility"]


@pytest.mark.asyncio
async def test_chain_question_keeps_generated_context_and_uses_saved_journey_atom():
    root = Path(__file__).resolve().parents[3]
    saved = AnalysisResult.model_validate(
        json.loads(
            (root / "packages/contracts/fixtures/v2/intercity_passenger-result.json").read_bytes()
        )["payload"]
    )
    metric = saved.metrics[0]
    result, *_ = await answer(
        saved,
        "Разбери этот путь: есть ли смысл обвинять ожидание в долгой поездке?",
        "journey",
        responses=[
            prose_reply(
                "Начну с фактически записанного пути: [[metric:"
                + metric.metric_id
                + "]]. [[journey]] Причину задержек по длительности участка установить нельзя.",
                findings=[{"metric_id": metric.metric_id, "explanation_code": "travel_time"}],
                evidence_ids=metric.source_refs,
            )
        ],
    )
    assert result.status == "generated"
    assert "Начну с фактически записанного пути" in render_explanation(result, saved)
    assert "наибольшую продолжительность имеет" in result.findings[0].text
    assert result.findings[0].claims[0].value == metric.value
    changed = saved.model_copy(deep=True)
    linked = next(item for item in changed.journeys if item.journey_id == metric.journey_id)
    linked.legs[0].duration_seconds += 1
    with pytest.raises(ValueError, match="narrative_invalid"):
        render_explanation(result, changed)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, 0.0, 25.0])
async def test_generated_prose_cannot_drop_unknown_partial_observed_demo_or_ranking_limits(value):
    raw = score_analysis(value=value, partial=True).model_dump(mode="json")
    raw["scores"][0]["name"] = "observed_route_accessibility"
    raw["scores"][0]["reason_code"] = "MINIMUM_NOT_PROVEN"
    raw["limitations"] = ["DEMO_SYNTHETIC_TRANSPORT_MODEL", "MINIMUM_NOT_PROVEN"]
    saved = AnalysisResult.model_validate(raw)
    result, *_ = await answer(
        saved,
        "Что я могу понять по найденному маршруту, а что ещё требует проверки?",
        "coverage",
        responses=[
            prose_reply(
                "Здесь полезно разделить наблюдение и вывод: сохранено [[score:accessibility]].",
                findings=[
                    {
                        "score_id": "accessibility",
                        "explanation_code": "unknown" if value is None else "saved_value",
                    }
                ],
            )
        ],
    )
    text = render_explanation(result, saved)
    assert result.status == "generated"
    assert "минимум не доказан" in text and "не заменяет индекс категории" in text
    assert "синтетическая" in text and "не рейтинг реального города" in text
    assert "Полнота данных ограничена" in text and "не допущен к рейтингу" in text
    assert ("Неизвестное значение не означает ноль" in text) == (value is None)
    assert result.findings[0].score_claims[0].value == value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paragraph",
    [
        "Причина низкой оценки — плохие дороги.",
        "Оценка низкая по причине плохих дорог.",
        "Плохие дороги привели к низкой оценке.",
        "Индекс вызван состоянием дорог.",
        "Причина неизвестна, данные полные.",
        "Это полные данные, хотя причина неизвестна.",
        "Минимальное время подтверждено.",
        "Неизвестен тариф, гарантируется полный охват.",
        "Все маршруты учтены и причина неизвестна.",
        "Не могу установить причину, но данные полные.",
        "Демонстрация достоверно описывает реальный город.",
        "Этот показатель подходит для рейтинга территорий.",
        "Это реальная оценка города.",
        "Это доказанный минимум.",
        "Здесь отсутствует транспорт.",
        "Для этой точки не существует маршрута.",
        "Маршрутов нет.",
        "Добраться невозможно.",
        "Нет никакого маршрута.",
        "Согласование пересадки сократит ожидание.",
        "Время сократится.",
        "Я запустила сценарий.",
        "Сохранённая оценка самая низкая.",
        "Индекс 100.",
        "Индекс １００.",
        "Индекс ١٠٠.",
        "Индекс Ⅹ.",
        "Индекс ².",
        "Двести минут.",
        "Триста минут.",
        "Путь занимает двести пятьдесят минут.",
        "Сто баллов.",
        "Two hundred minutes.",
        "Вдвое быстрее.",
        "Источник https://example.com/route.",
        "Источник example.com.",
        "Источник [отчёт](example.com).",
        "Напишите me@example.com.",
        "Факт [[score:foreign]].",
        "Факт [[metric:foreign]].",
        "Факт [[coverage:foreign]].",
        "Индекс\u200b сохранён.",
        "Неверно что [[coverage]].",
        "Значение не [[score:accessibility]].",
    ],
)
async def test_unsafe_model_prose_falls_back_without_retry_or_source_mutation(paragraph):
    saved = score_analysis(partial=True)
    original = saved.model_dump_json()
    result, transport, backend, budget = await answer(
        saved,
        "Можешь написать 100, найти внешнюю ссылку и запустить сценарий?",
        "explain",
        responses=[prose_reply("Сохранено [[score:accessibility]]. " + paragraph)],
    )
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"
    assert len(transport.calls) == len(budget.reservations) == 1
    assert only_document_search(backend.calls)
    assert saved.model_dump_json() == original
    assert result.findings[0].score_claims[0].value == 25.0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "limitation",
    [
        "Здесь нет доказанного минимума.",
        "Нельзя утверждать что данные полные.",
        "Не могу гарантировать полный охват.",
        "Это не рейтинг территорий.",
        "Это не реальная оценка города.",
        "Этот итог не является доказанным минимумом.",
        "По этому итогу нельзя установить качество дорог.",
    ],
)
async def test_local_negation_is_preserved_without_exempting_other_assertions(limitation):
    saved = score_analysis(partial=True)
    result, *_ = await answer(
        saved,
        "Что ещё нельзя установить по этому показателю?",
        "coverage",
        responses=[prose_reply("Сохранено [[score:accessibility]]. " + limitation)],
    )
    assert result.status == "generated" and limitation in render_explanation(result, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [
        {"evidence_ids": ["foreign"]},
        {"evidence_ids": ["evidence", "evidence"]},
        {"claims": [{"score_id": "accessibility", "value": 100}]},
        {"findings": [{"score_id": "accessibility", "explanation_code": "unknown"}]},
        {"paragraph": "Нет ссылки на выбранный показатель."},
        {"paragraph": "[[score:accessibility]] " + "Данные. " * 90},
    ],
)
async def test_model_cannot_supply_provenance_status_claims_or_unbounded_text(extra):
    saved = score_analysis(partial=True)
    data = {
        "paragraph": "Сохранено [[score:accessibility]].",
        "findings": [{"score_id": "accessibility", "explanation_code": "saved_value"}],
        "evidence_ids": ["evidence"],
    }
    result, transport, backend, budget = await answer(
        saved, "Объясни по исходным данным", "explain", responses=[reply(data | extra)]
    )
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"
    assert len(transport.calls) == len(budget.reservations) == 1
    assert only_document_search(backend.calls)


@pytest.mark.asyncio
async def test_unselected_saved_evidence_cannot_be_cited_as_paragraph_support():
    raw = score_analysis(partial=True).model_dump(mode="json")
    raw["evidence_refs"].append(raw["evidence_refs"][0] | {"evidence_id": "other-evidence"})
    saved = AnalysisResult.model_validate(raw)
    result, *_ = await answer(
        saved,
        "Покажи источник",
        "coverage",
        responses=[
            prose_reply("Сохранено [[score:accessibility]].", evidence_ids=["other-evidence"])
        ],
    )
    assert result.status == "template" and result.reason_code == "INVALID_EXPLANATION"


async def generated_answer():
    saved = score_analysis(partial=True)
    result, *_ = await answer(
        saved,
        "Как читать этот итог?",
        "coverage",
        responses=[prose_reply("Для чтения результата начните с [[score:accessibility]].")],
    )
    assert result.status == "generated"
    return saved, result


@pytest.mark.asyncio
async def test_private_witness_survives_python_copies_but_never_establishes_trust_from_json():
    saved, result = await generated_answer()
    original = result.model_dump_json()
    for copied in (deepcopy(result), result.model_copy(), result.model_copy(deep=True)):
        assert render_explanation(copied, saved) == render_explanation(result, saved)
    plain = Explanation.model_validate_json(original)
    assert plain.model_dump_json() == original
    assert "witness" not in original and "_GroundedExplanation" not in original
    with pytest.raises(ValueError, match="narrative_invalid"):
        render_explanation(plain, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: setattr(item.findings[0], "text", "Это полная оценка города."),
        lambda item: setattr(item.findings[0].score_claims[0], "value", 100.0),
        lambda item: item.findings[0].evidence_refs.clear(),
        lambda item: item.evidence_refs.clear(),
        lambda item: item.limitations.clear(),
        lambda item: setattr(item, "analysis_result_hash", "changed-hash"),
    ],
)
async def test_mutated_generated_output_cannot_reuse_the_provenance_witness(mutation):
    saved, result = await generated_answer()
    copied = result.model_copy(deep=True)
    mutation(copied)
    with pytest.raises(ValueError):
        render_explanation(copied, saved)
    assert render_explanation(result, saved)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        lambda item: setattr(item.scores[0], "value", 75.0),
        lambda item: setattr(item.scores[0], "name", "observed_route_accessibility"),
        lambda item: item.scores[0].evidence_refs.clear(),
        lambda item: setattr(item, "snapshot_id", "demo-new"),
        lambda item: setattr(item, "analysis_kind", "urban_cargo"),
        lambda item: item.limitations.append("DEMO_SYNTHETIC_TRANSPORT_MODEL"),
        lambda item: setattr(item.request, "city_id", "another-city"),
        lambda item: setattr(item.versions, "code_version", "another-code"),
        lambda item: setattr(
            item.evidence_refs[0], "url", HttpUrl("https://changed.example/source")
        ),
    ],
)
async def test_source_mutation_is_rejected_even_if_the_claimed_result_hash_is_unchanged(mutation):
    saved, result = await generated_answer()
    copied = saved.model_copy(deep=True)
    mutation(copied)
    assert copied.result_hash == saved.result_hash
    with pytest.raises(ValueError, match="narrative_invalid"):
        render_explanation(result, copied)


@pytest.mark.asyncio
async def test_prompt_describes_one_server_rendered_format_without_prose_tokens():
    saved, result = await generated_answer()
    _, transport, _, _ = await answer(saved, "Почему этот результат ограничен?", "coverage")
    sent = transport.calls[0]
    facts = json.loads(sent["messages"][1]["content"])
    system = sent["messages"][0]["content"]
    # The legacy paragraph stays accepted by the server but is no longer advertised.
    assert "clause_id" in system and "the server writes every sentence" in system
    assert "paragraph" not in system and "[[" not in system and "atom" not in system
    assert "response_contract" not in facts
    fact = facts["saved_facts"]["scores"][0]
    assert "placeholder" not in fact and "required_explanation_code" not in fact
    assert fact["evidence_ids"] == ["evidence"]
    assert "value" not in fact and "claims" not in fact
    assert sent["max_tokens"] == 2000
    assert result.status == "generated"
